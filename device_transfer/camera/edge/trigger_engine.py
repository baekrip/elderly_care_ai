"""Edge Trigger Engine — Phase 2.

Calculates trigger_flags per frame, judges candidate_type,
and manages ring buffers for recent feature history.

Decision 2 applied: multiple trigger composition (B) + zone/time context (D).
Daily lying (floor, sofa) does NOT immediately trigger DANGER; context is checked.
"""
from __future__ import annotations

from collections import deque
from typing import Any


# --- calibrated thresholds (2026-04-11 data) ---
THRESHOLDS: dict[str, Any] = {
    "torso_angle_spike_deg": 15.0,
    "torso_angle_sustained_deg": 100.0,
    "vertical_velocity_spike_px_s": 490.0,
    "center_velocity_spike_px_s": 540.0,
    "center_velocity_near_zero_px_s": 7.0,
    "inactivity_duration_sec": 300,
    "prolonged_floor_lying_duration_sec": 60,
    "bbox_aspect_ratio_change": 0.29,
    "shoulder_tilt_spike_deg": 150.0,
    "knee_angle_collapse_deg": 77.0,
    "pose_confidence_drop": 0.40,
    "visible_joint_drop": 0.45,
    "head_hip_y_diff_threshold": 0.0,
    "standing_sitting_osc_count": 5,
    "standing_sitting_osc_window_sec": 120,
    "gradual_fall_min_frames": 8,
    "gradual_fall_min_torso_delta_deg": 35.0,
    "gradual_fall_min_xgboost_prob": 0.4,
    "gradual_fall_min_head_drop_norm": 0.05,
}

# --- candidate type mapping ---
DANGER_TYPES = {
    "FORWARD_COLLAPSE_FROM_STANDING",
    "FORWARD_COLLAPSE_FROM_SITTING",
    "SIDEWAYS_COLLAPSE",
    "BACKWARD_FALL",
    "GRADUAL_COLLAPSE",
    "CHAIR_SLIDE_FALL",
    "BED_ROLL_FALL",
    "LOSS_OF_BALANCE",
    "SYNCOPE_COLLAPSE",
}

ABNORMAL_TYPES = {
    "GRADUAL_FALL_SUSPECT",
    "PROLONGED_FLOOR_LYING",
    "PROLONGED_INACTIVITY",
    "ABNORMAL_POSTURE_SUSTAINED",
    "REPETITIVE_STANDING_SITTING",
    "WANDERING_PATTERN",
    "NIGHT_UNUSUAL_ACTIVITY",
    "FAILED_STANDING_ATTEMPT",
}

QUALITY_TYPES = {
    "LOW_SKELETON_QUALITY",
    "OCCLUSION_SUSPECTED",
    "PERSON_DISAPPEARED",
}

# composite condition descriptions
_BROKEN_COMPOSITE_CONDITIONS_TEXT = r"""
COMPOSITE_CONDITIONS: dict[str, str] = {
    "FORWARD_COLLAPSE_FROM_STANDING": "서있음 → 몸통각도 급변 + 수직속도 급증 + 누움/바닥 도달",
    "FORWARD_COLLAPSE_FROM_SITTING": "앉아있음 → 몸통각도 급변 + 머리높이 급락 + bbox 종횡비 변화",
    "SIDEWAYS_COLLAPSE": "서있음/앉아있음 → 어깨기울기 급변 + 한쪽 hip 급락 + 누움",
    "BACKWARD_FALL": "서있음 → 몸통 후방기울기 + 수직속도 급증 + 누움",
    "GRADUAL_COLLAPSE": "서있음/앉아있음 → 몸통각도 서서히 증가(3~10초) + 무릎각도 감소 + 최종 누움",
    "CHAIR_SLIDE_FALL": "앉아있음 → bbox 중심 하강 + hip높이 급락 + 바닥 도달",
    "BED_ROLL_FALL": "누워있음(침대) → bbox 중심 급격 수평이동 + 수직속도 증가",
    "LOSS_OF_BALANCE": "서있음/걷기 → 중심 좌표 흔들림 + 속도 불규칙",
    "SYNCOPE_COLLAPSE": "서있음 → 갑작스런 전체 관절 confidence 급락 + 수직속도 최대",
    "PROLONGED_FLOOR_LYING": "바닥 누움 + 이동없음 → 지속시간 > 60초",
    "PROLONGED_INACTIVITY": "모든자세 + 속도≈0 → 지속시간 > 300초",
    "ABNORMAL_POSTURE_SUSTAINED": "몸통각도 45~70° 유지 + 불안정 상태 → 30초 이상",
    "REPETITIVE_STANDING_SITTING": "서있음↔앉아있음 반복 > 5회/2분",
    "WANDERING_PATTERN": "걷기 + 같은 영역 반복 → 5분 이상",
    "NIGHT_UNUSUAL_ACTIVITY": "야간(22~06시) + 걷기/서있음 → 지속",
    "FAILED_STANDING_ATTEMPT": "누워있음/앉아있음 → 일어서기 시도 → 다시 원위치 반복",
    "LOW_SKELETON_QUALITY": "visible_joint_ratio < 0.4 + pose_conf < 0.3 → 연속 5프레임",
    "OCCLUSION_SUSPECTED": "갑작스런 keypoint 수 급감 + bbox 유지",
    "PERSON_DISAPPEARED": "이전 프레임 대비 bbox 완전 소실 → 3초 이상",
}
"""

COMPOSITE_CONDITIONS: dict[str, str] = {
    "FORWARD_COLLAPSE_FROM_STANDING": "standing -> torso angle spike + velocity spike + floor/head cue",
    "FORWARD_COLLAPSE_FROM_SITTING": "sitting -> torso angle spike + head drop + bbox shape change",
    "SIDEWAYS_COLLAPSE": "standing/sitting -> shoulder tilt spike + side hip drop + fall cue",
    "BACKWARD_FALL": "standing -> backward torso tilt + velocity spike + fall cue",
    "GRADUAL_COLLAPSE": "standing/sitting -> gradual torso increase + knee angle decrease + lying",
    "CHAIR_SLIDE_FALL": "sitting -> bbox center drops + hip drop + floor cue",
    "BED_ROLL_FALL": "lying on bed -> horizontal bbox shift + vertical velocity increase",
    "LOSS_OF_BALANCE": "standing/walking -> unstable center coordinates + velocity irregularity",
    "SYNCOPE_COLLAPSE": "standing -> sudden whole-body confidence drop + vertical velocity peak",
    "PROLONGED_FLOOR_LYING": "lying + no movement longer than configured threshold",
    "PROLONGED_INACTIVITY": "any posture + near-zero movement longer than configured threshold",
    "ABNORMAL_POSTURE_SUSTAINED": "body angle 45-70 deg sustained for 30 sec or more",
    "REPETITIVE_STANDING_SITTING": "standing/sitting repeats over configured count window",
    "WANDERING_PATTERN": "walking + repeated area pattern for a long interval",
    "NIGHT_UNUSUAL_ACTIVITY": "night interval + walking/standing activity sustained",
    "FAILED_STANDING_ATTEMPT": "lying/sitting -> failed stand-up attempt repeated",
    "GRADUAL_FALL_SUSPECT": "slow torso angle increase + head descent + XGBoost fall probability",
    "LOW_SKELETON_QUALITY": "visible_joint_ratio < 0.4 + pose_conf < 0.3 sustained",
    "OCCLUSION_SUSPECTED": "sudden keypoint loss with stable bbox",
    "PERSON_DISAPPEARED": "previous bbox disappears for a configured duration",
}


class TriggerEngine:
    """Stateful trigger evaluator per track_id."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.thresholds = dict(THRESHOLDS)
        if config and "trigger_thresholds" in config:
            self.thresholds.update(config["trigger_thresholds"])
        time_context_cfg = (config or {}).get("time_context", {})
        self.enable_wall_clock_night_activity = bool(
            time_context_cfg.get("use_wall_clock_for_night_activity", False)
        )

        # per-track ring buffers (recent 2 seconds = 24 frames @12fps)
        self._buffers: dict[int, deque] = {}
        self._label_history: dict[int, deque] = {}
        self._inactivity_start: dict[int, int] = {}
        self._lying_start: dict[int, int] = {}
        self._disappeared_start: dict[int, float] = {}
        self._max_buffer = 24

    def _get_buffer(self, track_id: int) -> deque:
        if track_id not in self._buffers:
            self._buffers[track_id] = deque(maxlen=self._max_buffer)
            self._label_history[track_id] = deque(maxlen=60)
        return self._buffers[track_id]

    def update(self, track_id: int, features: dict[str, float],
               coarse_label: str, ts_ms: int) -> list[str]:
        """Evaluate trigger flags for one frame. Returns active flag names."""
        buf = self._get_buffer(track_id)
        buf.append({"features": features, "label": coarse_label, "ts_ms": ts_ms})
        self._label_history[track_id].append(coarse_label)

        flags: list[str] = []
        th = self.thresholds

        # --- instant triggers ---
        torso = features.get("torso_angle_deg", 0)
        torso_delta = features.get("torso_angle_delta", 0)
        vert_vel = features.get("vertical_velocity_px_s", 0)
        center_vel = features.get("center_velocity_px_s", 0)
        aspect = features.get("bbox_aspect_ratio", 0)
        shoulder = features.get("shoulder_tilt_deg", 0)
        l_knee = features.get("left_knee_angle_deg", 180)
        r_knee = features.get("right_knee_angle_deg", 180)
        pose_conf = features.get("pose_confidence_mean", 1.0)
        vis_ratio = features.get("visible_joint_ratio", 1.0)
        head_hip = features.get("head_hip_y_diff", -100)

        if abs(torso_delta) > th["torso_angle_spike_deg"]:
            flags.append("torso_angle_spike")

        if abs(torso) > th["torso_angle_sustained_deg"]:
            flags.append("torso_angle_sustained_high")

        if vert_vel > th["vertical_velocity_spike_px_s"]:
            flags.append("vertical_velocity_spike")

        if center_vel > th["center_velocity_spike_px_s"]:
            flags.append("center_velocity_spike")

        if len(buf) >= 2:
            prev_aspect = buf[-2]["features"].get("bbox_aspect_ratio", aspect)
            if abs(aspect - prev_aspect) > th["bbox_aspect_ratio_change"]:
                flags.append("bbox_aspect_ratio_change")

        if abs(shoulder) > th["shoulder_tilt_spike_deg"]:
            flags.append("shoulder_tilt_spike")

        if l_knee < th["knee_angle_collapse_deg"] and r_knee < th["knee_angle_collapse_deg"]:
            flags.append("knee_angle_collapse")

        if pose_conf < th["pose_confidence_drop"]:
            flags.append("pose_confidence_drop")

        if vis_ratio < th["visible_joint_drop"]:
            flags.append("visible_joint_drop")

        if head_hip > th["head_hip_y_diff_threshold"]:
            flags.append("head_below_hip")

        if self._check_gradual_fall(track_id):
            flags.append("gradual_fall_trend")

        # --- label transition ---
        if len(self._label_history[track_id]) >= 2:
            prev_label = self._label_history[track_id][-2]
            if prev_label != coarse_label:
                flags.append("coarse_label_transition")

        # --- duration-based triggers ---
        # Use video/device timestamps, not wall-clock processing time.
        # Otherwise replay speed changes the meaning of long-duration rules.
        now_ms = int(ts_ms)
        inactivity_duration_ms = float(th["inactivity_duration_sec"]) * 1000.0
        floor_lying_duration_ms = float(th["prolonged_floor_lying_duration_sec"]) * 1000.0

        # inactivity
        if center_vel < th["center_velocity_near_zero_px_s"]:
            if track_id not in self._inactivity_start:
                self._inactivity_start[track_id] = now_ms
            elif now_ms - self._inactivity_start[track_id] > inactivity_duration_ms:
                flags.append("inactivity_duration")
        else:
            self._inactivity_start.pop(track_id, None)

        # prolonged floor lying (Decision 2: daily lying awareness)
        bed_overlap = float(features.get("bed_roi_overlap", 0.0) or 0.0)
        floor_overlap = float(features.get("floor_roi_overlap", 0.0) or 0.0)
        is_bed_context = bed_overlap >= 0.5 and bed_overlap > floor_overlap
        if coarse_label == "LYING" and center_vel < th["center_velocity_near_zero_px_s"] and not is_bed_context:
            if track_id not in self._lying_start:
                self._lying_start[track_id] = now_ms
            elif now_ms - self._lying_start[track_id] > floor_lying_duration_ms:
                flags.append("prolonged_floor_lying")
        else:
            self._lying_start.pop(track_id, None)

        # standing-sitting oscillation
        hist = list(self._label_history[track_id])
        transitions = 0
        for i in range(1, len(hist)):
            if (hist[i] == "STANDING" and hist[i - 1] == "SITTING") or \
               (hist[i] == "SITTING" and hist[i - 1] == "STANDING"):
                transitions += 1
        if transitions > th["standing_sitting_osc_count"]:
            flags.append("standing_sitting_oscillation")

        return flags

    def judge_candidate(self, track_id: int, flags: list[str],
                        coarse_label: str) -> tuple[str, str, str] | None:
        """
        Determine candidate_type based on active flags + context.
        Returns (candidate_type, candidate_category, composite_condition) or None.

        Decision 2 applied: lying on floor/sofa without velocity spike
        does NOT trigger DANGER — only ABNORMAL after duration threshold.
        """
        if not flags:
            return None

        th = self.thresholds

        has_velocity = "vertical_velocity_spike" in flags or "center_velocity_spike" in flags
        has_torso = "torso_angle_spike" in flags
        has_head_below = "head_below_hip" in flags
        has_aspect = "bbox_aspect_ratio_change" in flags
        has_shoulder = "shoulder_tilt_spike" in flags
        has_knee = "knee_angle_collapse" in flags
        has_pose_drop = "pose_confidence_drop" in flags
        has_inactivity = "inactivity_duration" in flags
        has_floor_lying = "prolonged_floor_lying" in flags
        has_osc = "standing_sitting_oscillation" in flags
        label_transition = "coarse_label_transition" in flags

        prev_labels = list(self._label_history.get(track_id, []))
        prev_label = prev_labels[-2] if len(prev_labels) >= 2 else "UNKNOWN"

        # --- DANGER candidates ---
        if has_velocity and has_torso and coarse_label in ("LYING", "TRANSITION"):
            if prev_label == "STANDING":
                if has_shoulder:
                    ctype = "SIDEWAYS_COLLAPSE"
                else:
                    ctype = "FORWARD_COLLAPSE_FROM_STANDING"
                return ctype, "DANGER", COMPOSITE_CONDITIONS[ctype]

            if prev_label == "SITTING":
                ctype = "FORWARD_COLLAPSE_FROM_SITTING"
                return ctype, "DANGER", COMPOSITE_CONDITIONS[ctype]

        if has_velocity and has_pose_drop and coarse_label in ("LYING", "TRANSITION"):
            ctype = "SYNCOPE_COLLAPSE"
            return ctype, "DANGER", COMPOSITE_CONDITIONS[ctype]

        if has_knee and has_torso and coarse_label in ("LYING", "TRANSITION", "SITTING"):
            ctype = "GRADUAL_COLLAPSE"
            return ctype, "DANGER", COMPOSITE_CONDITIONS[ctype]

        if has_velocity and has_head_below and prev_label == "SITTING":
            ctype = "CHAIR_SLIDE_FALL"
            return ctype, "DANGER", COMPOSITE_CONDITIONS[ctype]

        # loss of balance (velocity oscillation without fall)
        if has_velocity and not has_torso and coarse_label in ("STANDING", "WALKING"):
            ctype = "LOSS_OF_BALANCE"
            return ctype, "DANGER", COMPOSITE_CONDITIONS[ctype]

        # --- ABNORMAL candidates ---
        # Decision 2: lying on floor is ABNORMAL, not DANGER, unless velocity spike
        if has_floor_lying and not has_velocity:
            ctype = "PROLONGED_FLOOR_LYING"
            return ctype, "ABNORMAL", COMPOSITE_CONDITIONS[ctype]

        if has_inactivity:
            ctype = "PROLONGED_INACTIVITY"
            return ctype, "ABNORMAL", COMPOSITE_CONDITIONS[ctype]

        if has_osc:
            ctype = "REPETITIVE_STANDING_SITTING"
            return ctype, "ABNORMAL", COMPOSITE_CONDITIONS[ctype]

        if "gradual_fall_trend" in flags:
            ctype = "GRADUAL_FALL_SUSPECT"
            return ctype, "ABNORMAL", COMPOSITE_CONDITIONS[ctype]

        # Night activity needs real capture time and baseline context.
        # Keep it opt-in on edge so offline replays are deterministic.
        if self.enable_wall_clock_night_activity:
            import datetime

            hour = datetime.datetime.now().hour
            recent_buffer = list(self._buffers.get(track_id, []))
            recent_center_velocities = [
                float(item["features"].get("center_velocity_px_s", 0.0))
                for item in recent_buffer[-12:]
            ]
            if (hour >= 22 or hour < 6) and coarse_label in ("WALKING", "STANDING"):
                if recent_center_velocities and max(recent_center_velocities) > th["center_velocity_near_zero_px_s"]:
                    ctype = "NIGHT_UNUSUAL_ACTIVITY"
                    return ctype, "ABNORMAL", COMPOSITE_CONDITIONS[ctype]

        # --- QUALITY candidates ---
        if "pose_confidence_drop" in flags and "visible_joint_drop" in flags:
            ctype = "LOW_SKELETON_QUALITY"
            return ctype, "QUALITY", COMPOSITE_CONDITIONS[ctype]

        # if we had flags but couldn't match a specific type, don't generate
        return None

    def cleanup_track(self, track_id: int) -> None:
        """Remove state for expired track_id."""
        self._buffers.pop(track_id, None)
        self._label_history.pop(track_id, None)
        self._inactivity_start.pop(track_id, None)
        self._lying_start.pop(track_id, None)
        self._disappeared_start.pop(track_id, None)

    def _check_gradual_fall(self, track_id: int) -> bool:
        th = self.thresholds
        min_frames = int(th.get("gradual_fall_min_frames", 8))
        recent = list(self._buffers.get(track_id, []))[-min_frames:]
        if len(recent) < min_frames:
            return False

        first_features = recent[0]["features"]
        last_features = recent[-1]["features"]
        torso_delta = float(last_features.get("torso_angle_deg", 0.0)) - float(first_features.get("torso_angle_deg", 0.0))
        if torso_delta < float(th.get("gradual_fall_min_torso_delta_deg", 35.0)):
            return False

        xgboost_prob = float(last_features.get("xgboost_prob", 0.0) or 0.0)
        if xgboost_prob < float(th.get("gradual_fall_min_xgboost_prob", 0.4)):
            return False

        first_head = first_features.get("nose_y_norm")
        last_head = last_features.get("nose_y_norm")
        if first_head is None or last_head is None:
            first_head = first_features.get("head_hip_y_diff")
            last_head = last_features.get("head_hip_y_diff")
            head_drop = float(last_head or 0.0) - float(first_head or 0.0)
            return head_drop > 0.0

        head_drop_norm = float(last_head) - float(first_head)
        return head_drop_norm >= float(th.get("gradual_fall_min_head_drop_norm", 0.05))
