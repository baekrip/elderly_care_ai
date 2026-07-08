from __future__ import annotations

from typing import Any

import time

from edge.tier_classifier import TierClassifier
from server.services.event_queue import EventQueue
from server.services.model_input_window import (
    ModelInputWindow,
    ModelInputWindowBuilder,
    fall_probability,
    fuse_model_outputs,
)
from server.services.risk_smoothing import RiskSmoother, to_risk_score
from shared.protocol import SkeletonFrame, SkeletonFrameBatch
from shared.time_utils import utc_iso_now


class Pi5SkeletonPipeline:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        archive: Any | None = None,
        stgcn_classifier: Any | None = None,
    ) -> None:
        self.config = config
        self.archive = archive
        self.stgcn_classifier = stgcn_classifier
        risk_cfg = config.get("risk_features", {})
        self.running_speed_threshold = float(risk_cfg.get("running_speed_threshold_px_s", 140.0))
        self.danger_speed_threshold = float(risk_cfg.get("danger_speed_threshold_px_s", 260.0))
        self.collision_overlap_threshold = float(risk_cfg.get("collision_overlap_threshold", 0.35))
        self.collision_jerk_threshold = float(risk_cfg.get("collision_jerk_threshold", 180.0))
        self.static_threshold_ms = float(risk_cfg.get("static_threshold_ms", 30_000.0))
        self.fall_torso_delta_threshold = float(risk_cfg.get("fall_torso_delta_threshold", 35.0))
        self.fall_head_drop_threshold = float(risk_cfg.get("fall_head_drop_threshold", 0.05))
        # Floor ROI + long inactivity rule (방안 C)
        self.floor_roi_lying_threshold = float(risk_cfg.get("floor_roi_lying_threshold", 0.4))
        self.floor_inactivity_threshold_ms = float(risk_cfg.get("floor_inactivity_threshold_ms", 60_000.0))
        self.pose_lost_threshold_ms = float(risk_cfg.get("pose_lost_threshold_ms", 10_000.0))
        self.low_confidence_threshold = float(risk_cfg.get("low_confidence_threshold", 0.15))
        self.min_rule_pose_confidence = float(risk_cfg.get("min_rule_pose_confidence", 0.20))
        self.min_rule_visible_joint_ratio = float(risk_cfg.get("min_rule_visible_joint_ratio", 0.30))
        self.lying_bbox_aspect_ratio_threshold = float(
            risk_cfg.get("lying_bbox_aspect_ratio_threshold", 1.20)
        )
        self.lying_static_duration_ms = float(risk_cfg.get("lying_static_duration_ms", 500.0))
        self.collision_pose_delta_threshold = float(risk_cfg.get("collision_pose_delta_threshold", 20.0))
        self.collision_instability_threshold = float(risk_cfg.get("collision_instability_threshold", 10.0))
        self.collision_bbox_area_change_threshold = float(
            risk_cfg.get("collision_bbox_area_change_threshold", 0.25)
        )
        self.fall_motion_velocity_threshold = float(
            risk_cfg.get("fall_motion_velocity_threshold_px_s", 50.0)
        )
        self.fall_motion_bbox_area_change_threshold = float(
            risk_cfg.get("fall_motion_bbox_area_change_threshold", 0.15)
        )
        self.fall_motion_pose_delta_threshold = float(
            risk_cfg.get("fall_motion_pose_delta_threshold", 10.0)
        )
        fusion_cfg = config.get("model_fusion", {})
        self.fusion_enabled = bool(fusion_cfg.get("enabled", True))
        self.stgcn_prefilter_min_xgboost_probability = float(
            fusion_cfg.get("stgcn_prefilter_min_xgboost_probability", 0.4)
        )
        self.stgcn_weight = float(fusion_cfg.get("stgcn_weight", 0.6))
        self.xgboost_weight = float(fusion_cfg.get("xgboost_weight", 0.4))
        self.decision_threshold = float(fusion_cfg.get("decision_threshold", 0.7))
        self.model_input_builder = ModelInputWindowBuilder(
            window_ms=int(config.get("stgcn", {}).get("window_ms", 5_000)),
            min_frames=int(config.get("stgcn", {}).get("min_window_frames", 8)),
            stgcn_stride_ms=int(fusion_cfg.get("stgcn_stride_ms", 1_000)),
        )
        self.tier_classifier = TierClassifier(config)
        smoothing_cfg = config.get("risk_smoothing", {})
        self.smoother = RiskSmoother(
            alpha=float(smoothing_cfg.get("alpha", 0.3)),
            suspicious_threshold=float(smoothing_cfg.get("suspicious_threshold", 0.45)),
            danger_threshold=float(smoothing_cfg.get("danger_threshold", 0.70)),
            vote_window=int(smoothing_cfg.get("vote_window", 10)),
        )
        self.events = EventQueue(
            confirm_after_ms=int(config.get("events", {}).get("confirm_after_ms", 30_000)),
            resolve_after_ms=int(config.get("events", {}).get("resolve_after_ms", 5_000)),
        )
        # [TEMP] fall_only_mode: true 이면 fall_detected 외 비정상자세 이벤트를 정상으로 억제
        # 복구 방법: config.orin.yaml 의 events.fall_only_mode 를 false 로 변경 후 서비스 재시작
        self.fall_only_mode: bool = bool(config.get("events", {}).get("fall_only_mode", False))
        self._fall_only_allowed: frozenset[str] = frozenset({"fall_detected", "normal_activity", "normal_activity_summary", "lying_down", "abnormal_posture"})
        # 정상(NORMAL) 상태 하트비트 전송 주기 설정
        # 평시 완전 정상 상태에서도 10초에 1번씩 normal_activity_summary 이벤트를 전송하여
        # 위험점수 평균 계산 시 분모(모수) 왜곡을 방지하고 "모든 행동 추론" 목적을 충족시킴
        self._normal_heartbeat_interval_sec: float = float(
            config.get("events", {}).get("normal_heartbeat_interval_sec", 10.0)
        )
        # 카메라 ID + 트랙 ID 별 마지막 정상 하트비트 전송 시각(단조 시계, perf_counter 기준)
        self._last_normal_heartbeat_ts: dict[str, float] = {}

    def handle_batch(self, batch: SkeletonFrameBatch) -> dict[str, Any]:
        results = [self.handle_frame(frame) for frame in batch.frames]
        return {"processed": len(results), "events": [result for result in results if result is not None]}

    def handle_frame(self, frame: SkeletonFrame) -> dict[str, Any] | None:
        model_window = self.model_input_builder.add(frame)
        model_outputs = self._analyze_model_window(model_window) if self.fusion_enabled and model_window else None
        
        # [POSE LOST FALLBACK GATING] Pi5 가 포즈를 유실하여 이전 값을 복사해 보내는 상태라면 플래그만 마킹
        is_pose_lost_fallback = False
        if frame.features and isinstance(frame.features, dict):
            if frame.features.get("pose_lost_fallback") or "pose-lost" in str(frame.frame_id):
                is_pose_lost_fallback = True

        event_type, raw_score = self._score_frame(frame)

        # [TEMP] fall_only_mode: fall_detected 외 이벤트는 normal_activity_summary 로 강제 변환
        if self.fall_only_mode and event_type not in self._fall_only_allowed:
            event_type = "normal_activity_summary"
            raw_score = 0.0
        if model_outputs:
            fusion = model_outputs["fusion"]
            fused_score = float(fusion["final_fall_probability"])
            
            # [SENSITIVITY OPT] 규칙 기반 낙상 감지 시, XGBoost 또는 융합 모델이 지지(누움/낙상 경향성)한다면
            # 융합 스코어 미달로 인한 억울한 정상 차단을 방지하기 위해 스코어를 확정선(0.66)으로 격상
            if event_type == "fall_detected":
                xgboost_label = model_outputs.get("xgboost", {}).get("label")
                if xgboost_label in {"LYING", "FALL", "GRADUAL_FALL"} or fused_score > 0.2:
                    raw_score = max(raw_score, 0.66)

            if self._model_outputs_contradict_motion_rule(event_type, model_outputs):
                event_type = "normal_activity_summary"
                raw_score = 0.0
            elif fused_score > raw_score:
                if self.fall_only_mode and fused_score < self.decision_threshold:
                    pass
                else:
                    event_type = "fall_detected" if fused_score > 0 else event_type
                    raw_score = fused_score
        if event_type == "normal_activity_summary" and raw_score == 0.0:
            self._resolve_competing_track_events(frame)
        key = f"{frame.camera_id}:{frame.track_id}:{event_type}"
        smoothed = self.smoother.update(key, raw_score)
        managed = self.events.update(
            camera_id=frame.camera_id,
            person_id=str(frame.track_id),
            event_type=event_type,
            risk_level=smoothed.level,
            risk_score=smoothed.ema_risk,
            timestamp_ms=frame.timestamp_ms,
            is_pose_lost=is_pose_lost_fallback,
        )
        if smoothed.level == "normal" and managed.state == "NORMAL":
            # 10초 주기 정상 하트비트: fall_only_mode 가 활성화되어 있을 때는 우회하여 실시간 전송 보장
            if not self.fall_only_mode:
                hb_key = f"{frame.camera_id}:{frame.track_id}"
                now = time.perf_counter()
                last_hb = self._last_normal_heartbeat_ts.get(hb_key, 0.0)
                if now - last_hb < self._normal_heartbeat_interval_sec:
                    return None
                self._last_normal_heartbeat_ts[hb_key] = now
        final_risk_label = smoothed.level
        final_state = managed.state
        final_risk_score = to_risk_score(smoothed.ema_risk)

        if self.fall_only_mode and final_risk_label in {"suspicious", "abnormal"}:
            final_risk_label = "normal"
            final_state = "NORMAL"
            final_risk_score = 1
            if model_outputs is not None:
                if "fusion" in model_outputs:
                    model_outputs["fusion"]["final_label"] = "normal"
                    model_outputs["fusion"]["final_fall_probability"] = 0.0
                if "xgboost" in model_outputs:
                    model_outputs["xgboost"]["label"] = "NORMAL"
                    model_outputs["xgboost"]["probability"] = 1.0
                if "stgcn" in model_outputs and isinstance(model_outputs["stgcn"], dict):
                    model_outputs["stgcn"]["label"] = "NORMAL"
                    model_outputs["stgcn"]["probability"] = 1.0

        result = {
            "event_id": managed.event_id,
            "camera_id": frame.camera_id,
            "frame_id": frame.frame_id,
            "track_id": frame.track_id,
            "event_type": event_type if final_risk_label != "normal" else "normal_activity_summary",
            "state": final_state,
            "risk_label": final_risk_label,
            "risk_score": final_risk_score,
            "risk_confidence": round(smoothed.ema_risk, 4),
            "raw_score": round(raw_score, 4),
            "vote_ratio": round(smoothed.vote_ratio, 4),
            "timestamp_ms": frame.timestamp_ms,
            "capture_ts": frame.capture_ts,
            "analysis_ts": utc_iso_now(),
        }
        if model_outputs is not None:
            result["model_outputs"] = model_outputs
        if self.archive is not None:
            self.archive.write_stgcn_result(result)
        return result

    def _resolve_competing_track_events(self, frame: SkeletonFrame) -> None:
        for event in self.events.active_events():
            if event.camera_id != frame.camera_id or event.person_id != str(frame.track_id):
                continue
            if event.event_type == "normal_activity":
                continue
            self.smoother.reset(f"{frame.camera_id}:{frame.track_id}:{event.event_type}")
        self.events.resolve_competing_events(
            camera_id=frame.camera_id,
            person_id=str(frame.track_id),
            keep_event_type="normal_activity",
            timestamp_ms=frame.timestamp_ms,
        )

    def _analyze_model_window(self, window: ModelInputWindow | None) -> dict[str, Any] | None:
        if window is None:
            return None

        xgboost_started = time.perf_counter()
        xgboost_label, xgboost_probability = self.tier_classifier.classify_summary(window.xgboost_summary)
        xgboost_inference_ms = round((time.perf_counter() - xgboost_started) * 1000.0, 4)
        xgboost_fall_probability = fall_probability(xgboost_label, xgboost_probability)

        stgcn_label: str | None = None
        stgcn_probability: float | None = None
        stgcn_output: dict[str, Any]
        if self.stgcn_classifier is None:
            stgcn_output = {"status": "skipped", "reason": "stgcn_classifier_not_configured"}
        elif xgboost_fall_probability < self.stgcn_prefilter_min_xgboost_probability:
            stgcn_output = {"status": "skipped", "reason": "xgboost_prefilter_below_threshold"}
        else:
            stgcn_started = time.perf_counter()
            stgcn_label, stgcn_probability, detail = self.stgcn_classifier.classify(
                window.with_coarse(xgboost_label, xgboost_probability)
            )
            stgcn_output = {
                "status": "ok",
                "label": stgcn_label,
                "probability": round(float(stgcn_probability), 6),
                "fall_probability": fall_probability(stgcn_label, stgcn_probability),
                "model": "stgcn" if getattr(self.stgcn_classifier, "model", None) is not None else "stgcn_stub",
                "backend": getattr(self.stgcn_classifier, "backend", None),
                "inference_time_ms": round((time.perf_counter() - stgcn_started) * 1000.0, 4),
                "detail": detail,
                "runtime": self.stgcn_classifier.runtime_status()
                if hasattr(self.stgcn_classifier, "runtime_status")
                else {},
            }

        return {
            "track_window": window.track_window,
            "xgboost": {
                "label": xgboost_label,
                "probability": round(float(xgboost_probability), 6),
                "fall_probability": xgboost_fall_probability,
                "model": str(getattr(self.tier_classifier, "model_path", "")),
                "inference_time_ms": xgboost_inference_ms,
            },
            "stgcn": stgcn_output,
            "fusion": fuse_model_outputs(
                xgboost_label=xgboost_label,
                xgboost_probability=xgboost_probability,
                stgcn_label=stgcn_label,
                stgcn_probability=stgcn_probability,
                stgcn_weight=self.stgcn_weight,
                xgboost_weight=self.xgboost_weight,
                decision_threshold=self.decision_threshold,
            ),
        }

    def _score_frame(self, frame: SkeletonFrame) -> tuple[str, float]:
        features = frame.features
        static_duration = float(features.get("static_duration_ms", 0.0))
        bed_overlap = float(features.get("bed_roi_overlap", 0.0) or 0.0)
        floor_overlap = float(features.get("floor_roi_overlap", 0.0) or 0.0)
        is_bed_context = bed_overlap >= 0.5 and bed_overlap > floor_overlap
        pose_confidence = float(
            features.get("pose_confidence_mean", features.get("pose_confidence", frame.pose_confidence_mean))
        )
        visible_joint_ratio = float(features.get("visible_joint_ratio", features.get("visibility_ratio", 1.0)))
        bbox_aspect_ratio = float(features.get("bbox_aspect_ratio", 0.0) or 0.0)
        fall_motion_confirmed = (
            abs(float(features.get("vertical_velocity_px_s", 0.0))) >= self.fall_motion_velocity_threshold
            or abs(float(features.get("center_velocity_px_s", 0.0))) >= self.fall_motion_velocity_threshold
            or abs(float(features.get("bbox_area_change", 0.0))) >= self.fall_motion_bbox_area_change_threshold
            or abs(float(features.get("pose_delta_mean", 0.0))) >= self.fall_motion_pose_delta_threshold
        )
        
        # [STATIC LYING BYPASS] 정적 누움 상태(움직임 소실 후 누운 자세)도 낙상 징후로 포섭
        is_lying_state = (
            fall_motion_confirmed
            or static_duration >= 1500.0
            or str(features.get("coarse_action", "")).upper() in {"LYING", "FALL"}
            or str(features.get("action_label", "")).upper() in {"LYING", "FALL"}
        )

        # [LYING BYPASS] 종횡비가 미달이더라도 포즈 분류 결과가 명확히 LYING/FALL 이면 낙상 징후로 포섭
        is_aspect_ratio_satisfied = (
            bbox_aspect_ratio >= self.lying_bbox_aspect_ratio_threshold
            or str(features.get("coarse_action", "")).upper() in {"LYING", "FALL"}
            or str(features.get("action_label", "")).upper() in {"LYING", "FALL"}
        )

        if not self._has_reliable_rule_pose(frame):
            if (
                not is_bed_context
                and is_aspect_ratio_satisfied
                and is_lying_state
            ):
                score = min(
                    0.55 + (bbox_aspect_ratio / max(self.lying_bbox_aspect_ratio_threshold * 3.0, 1.0)) * 0.30,
                    0.85,
                )
                return "fall_detected", score
            if pose_confidence < self.low_confidence_threshold and static_duration >= self.pose_lost_threshold_ms:
                score = min(static_duration / max(self.pose_lost_threshold_ms * 3.0, 1.0), 0.8)
                return "pose_lost_inactivity", score
            return "normal_activity", 0.0

        if (
            not is_bed_context
            and is_aspect_ratio_satisfied
            and is_lying_state
        ):
            score = min(
                0.55 + (bbox_aspect_ratio / max(self.lying_bbox_aspect_ratio_threshold * 3.0, 1.0)) * 0.30,
                0.85,
            )
            return "fall_detected", score

        speed = abs(float(features.get("center_velocity_px_s", 0.0)))
        if speed >= self.running_speed_threshold:
            score = 0.5 + min(speed / max(self.danger_speed_threshold, 1.0), 1.0) * 0.5
            return "running_over_speed", min(score, 1.0)

        overlap = float(features.get("bbox_overlap_ratio", 0.0))
        jerk = abs(float(features.get("jerk_score", 0.0)))
        collision_motion_confirmed = (
            abs(float(features.get("pose_delta_mean", 0.0))) >= self.collision_pose_delta_threshold
            or abs(float(features.get("instability_score", 0.0))) >= self.collision_instability_threshold
            or abs(float(features.get("bbox_area_change", 0.0))) >= self.collision_bbox_area_change_threshold
        )
        if overlap >= self.collision_overlap_threshold or (
            jerk >= self.collision_jerk_threshold and collision_motion_confirmed
        ):
            score = max(overlap, min(jerk / max(self.collision_jerk_threshold, 1.0), 1.0))
            return "collision_suspected", min(score, 1.0)

        if (
            not is_bed_context
            and bbox_aspect_ratio >= self.lying_bbox_aspect_ratio_threshold
            and static_duration >= self.lying_static_duration_ms
        ):
            score = min(
                0.45 + (bbox_aspect_ratio / max(self.lying_bbox_aspect_ratio_threshold * 2.0, 1.0)) * 0.35,
                0.85,
            )
            return "fall_detected", score

        if static_duration >= self.static_threshold_ms and not is_bed_context:
            return "faint_static", min(static_duration / max(self.static_threshold_ms * 2.0, 1.0), 1.0)

        torso_delta = _circular_abs_delta(float(features.get("torso_angle_delta", 0.0)))
        head_drop = abs(float(features.get("head_drop_norm", 0.0)))
        torso_fall_confirmed = torso_delta >= self.fall_torso_delta_threshold and fall_motion_confirmed
        if torso_fall_confirmed or head_drop >= self.fall_head_drop_threshold:
            score = max(
                min(torso_delta / max(self.fall_torso_delta_threshold * 2.0, 1.0), 1.0),
                min(head_drop / max(self.fall_head_drop_threshold * 2.0, 1.0), 1.0),
            )
            return "fall_detected", score

        # ── 방안 C: 바닥 ROI 장시간 미움직임 + 저confidence 보완 규칙 ──
        is_floor_context = floor_overlap >= self.floor_roi_lying_threshold and floor_overlap > bed_overlap

        # 바닥 ROI에서 오래 움직이지 않고 pose confidence가 낮으면 lying_on_floor_uncertain
        if is_floor_context and static_duration >= self.floor_inactivity_threshold_ms:
            score = min(static_duration / max(self.floor_inactivity_threshold_ms * 2.0, 1.0), 1.0)
            return "lying_on_floor_uncertain", score

        # pose가 거의 감지되지 않는 상태가 오래 지속되면 (가려짐/누움 의심)
        if pose_confidence < self.low_confidence_threshold and static_duration >= self.pose_lost_threshold_ms:
            score = min(static_duration / max(self.pose_lost_threshold_ms * 3.0, 1.0), 0.8)
            return "pose_lost_inactivity", score

        return "normal_activity", 0.0

    def _has_reliable_rule_pose(self, frame: SkeletonFrame) -> bool:
        features = frame.features
        pose_confidence = float(
            features.get("pose_confidence_mean", features.get("pose_confidence", frame.pose_confidence_mean))
        )
        visible_joint_ratio = float(features.get("visible_joint_ratio", features.get("visibility_ratio", 1.0)))
        return (
            pose_confidence >= self.min_rule_pose_confidence
            and visible_joint_ratio >= self.min_rule_visible_joint_ratio
        )

    def _model_outputs_contradict_motion_rule(self, event_type: str, model_outputs: dict[str, Any]) -> bool:
        if event_type not in {"running_over_speed", "collision_suspected"}:
            return False
        fusion = model_outputs.get("fusion") or {}
        xgboost = model_outputs.get("xgboost") or {}
        stgcn = model_outputs.get("stgcn") or {}
        return (
            str(fusion.get("final_label", "")).lower() == "normal"
            and float(fusion.get("final_fall_probability", 1.0) or 0.0) <= 0.1
            and str(xgboost.get("label", "")).upper() == "NORMAL"
            and float(xgboost.get("probability", 0.0) or 0.0) >= 0.9
            and str(stgcn.get("label", "")).upper() in {"STANDING", "SITTING"}
        )


def _circular_abs_delta(value: float) -> float:
    delta = abs(float(value)) % 360.0
    return min(delta, 360.0 - delta)
