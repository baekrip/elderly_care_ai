from __future__ import annotations

import os
import argparse
import ipaddress
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from edge.action_classifier import ActionClassifier
from edge.async_pose import LatestPoseInferenceWorker, PoseInferenceOutput
from edge.candidate_sender import CandidateSender
from edge.camera import CameraSource
from edge.clip_manager import ClipManager
from edge.clip_rest_server import ClipRequestHandler, ClipRestServer
from edge.config import load_config
from edge.event_listener import EdgeEventListener
from edge.feature_extractor import FeatureExtractor
from edge.local_output import LocalOutputWriter
from edge.pose_estimator import YoloPoseEstimator
from edge.preprocess import FramePreprocessor
from edge.rtsp_streamer import RTSPStreamer
from edge.sender import ServerClient
from edge.tier_classifier import TierClassifier
from edge.timeline import TimelineBuilder
from edge.tracker import SimpleTracker
from edge.trigger_engine import TriggerEngine
from edge.video_buffer import RollingVideoBuffer
from edge.ws_sender import SkeletonWebSocketSender
from shared.protocol import (
    ActivityFrame,
    ActivityFrameBatch,
    BoundingBox,
    CameraRegistration,
    CandidateClipRef,
    CandidateWindow,
    CandidateWindowFrame,
    Keypoint,
    PosePerson,
    SkeletonFrame,
    SkeletonFrameBatch,
    TimelineBatch,
    VideoRef,
)
from shared.time_utils import utc_iso_from_ms, utc_iso_now


LOGGER = logging.getLogger("edge")

def _load_project_dotenv() -> None:
    """Load project .env values without overriding already exported environment variables."""
    candidates = [
        Path(__file__).resolve().parents[1] / ".env",
        Path.cwd() / ".env",
    ]

    for dotenv_path in candidates:
        if not dotenv_path.exists():
            continue

        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()

            if key.startswith("export "):
                key = key[len("export "):].strip()

            if not key:
                continue

            value = value.strip()

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ.setdefault(key, value)

        return


def _override_secret_from_env(
    section: dict[str, Any],
    *,
    value_key: str,
    env_key: str,
    default_env_name: str,
) -> None:
    env_name = str(section.get(env_key) or default_env_name).strip()
    value = os.getenv(env_name, "").strip()
    if value:
        section[value_key] = value


def apply_runtime_env_overrides(config: dict[str, Any]) -> None:
    ec2_stream_host = os.getenv("BACKEND_URL", "").strip() or os.getenv("EC2_STREAM_HOST", "").strip()

    if ec2_stream_host:
        ec2_stream_host = ec2_stream_host.replace("http://", "").replace("https://", "").strip("/")
        rtsp_output_url = f"rtsp://{ec2_stream_host}:8554/P001"
        camera_stream_url = f"http://{ec2_stream_host}:8889/P001/"

        config.setdefault("stream", {})
        config["stream"]["output_url"] = rtsp_output_url

        config.setdefault("camera", {})
        config["camera"]["stream_url"] = camera_stream_url

        LOGGER.info("stream.output_url set from BACKEND_URL/EC2_STREAM_HOST")
        LOGGER.info("camera.stream_url set from BACKEND_URL/EC2_STREAM_HOST")

    rtsp_output_url = os.getenv("RTSP_OUTPUT_URL", "").strip()
    if rtsp_output_url:
        config.setdefault("stream", {})
        config["stream"]["output_url"] = rtsp_output_url
        LOGGER.info("stream.output_url overridden by RTSP_OUTPUT_URL")

    camera_stream_url = os.getenv("CAMERA_STREAM_URL", "").strip()
    if camera_stream_url:
        config.setdefault("camera", {})
        config["camera"]["stream_url"] = camera_stream_url
        LOGGER.info("camera.stream_url overridden by CAMERA_STREAM_URL")

    server_cfg = config.setdefault("server", {})
    _override_secret_from_env(
        server_cfg,
        value_key="ingest_api_key",
        env_key="ingest_api_key_env",
        default_env_name="EDGE_INGEST_API_KEY",
    )
    _override_secret_from_env(
        server_cfg,
        value_key="clip_upload_api_key",
        env_key="clip_upload_api_key_env",
        default_env_name="CLIP_UPLOAD_API_KEY",
    )

    clip_request_cfg = config.setdefault("clip_request_server", {})
    _override_secret_from_env(
        clip_request_cfg,
        value_key="api_key",
        env_key="api_key_env",
        default_env_name="EDGE_CLIP_REQUEST_API_KEY",
    )

def _host_is_private_or_placeholder(host: str) -> bool:
    normalized = str(host or "").strip()
    if not normalized:
        return True
    if normalized in {"ORIN_IP", "PI5_IP", "localhost", "orin", "pi5cam1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_private or ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _url_host(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    return parsed.hostname or ""


def validate_runtime_security(config: dict[str, Any]) -> None:
    runtime_role = str(config.get("runtime", {}).get("role", "full_edge"))
    if runtime_role != "skeleton_sender":
        return

    server_cfg = config.get("server", {}) or {}
    base_url = str(server_cfg.get("base_url", "") or "")
    if base_url and not _host_is_private_or_placeholder(_url_host(base_url)):
        raise ValueError(f"skeleton_sender external server.base_url is not allowed: {base_url}")

    skeleton_ws_url = str(server_cfg.get("skeleton_ws_url", "") or "")
    if skeleton_ws_url and not _host_is_private_or_placeholder(_url_host(skeleton_ws_url)):
        raise ValueError(f"skeleton_sender external skeleton_ws_url is not allowed: {skeleton_ws_url}")

    clip_request_cfg = config.get("clip_request_server", {}) or {}
    if bool(clip_request_cfg.get("enabled", False)):
        bind_host = str(clip_request_cfg.get("host", "") or "")
        if bind_host not in {"0.0.0.0", "::"} and not _host_is_private_or_placeholder(bind_host):
            raise ValueError(f"skeleton_sender clip_request_server.host must be LAN/private: {bind_host}")


def _normalize_feature_map(feature_map: dict[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for key, value in feature_map.items():
        try:
            normalized[key] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized


def _apply_processing_resolution(frame: Any, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    processing_resolution = config["camera"].get("processing_resolution")
    if not processing_resolution:
        return frame, {}
    import cv2

    width, height = int(processing_resolution[0]), int(processing_resolution[1])
    resized = cv2.resize(frame, (width, height))
    return resized, {"processing_resolution": [width, height]}


def _run_pose_inference(
    frame: Any,
    config: dict[str, Any],
    preprocessor: FramePreprocessor,
    pose_estimator: YoloPoseEstimator,
) -> PoseInferenceOutput:
    processing_frame, processing_meta = _apply_processing_resolution(frame, config)
    preprocess_result = preprocessor.apply(processing_frame)
    preprocess_result.metadata.update(processing_meta)
    frame_for_inference = preprocess_result.frame
    pose_started_at = time.perf_counter()
    detections = pose_estimator.predict(frame_for_inference)
    latency_ms = (time.perf_counter() - pose_started_at) * 1000.0
    return PoseInferenceOutput(
        detections=detections,
        frame_for_inference=frame_for_inference,
        preprocess_metadata=dict(preprocess_result.metadata),
        latency_ms=latency_ms,
    )


def _build_perf_stats_row(
    *,
    camera_id: str,
    started_at: float,
    ended_at: float,
    frame_count: int,
    pose_confidence_sum: float,
    pose_confidence_count: int,
    candidate_count: int,
    target_fps: float | None = None,
    inference_frame_count: int | None = None,
    pose_latency_ms: list[float] | None = None,
    loop_latency_ms: list[float] | None = None,
    frame_interval_ms: list[float] | None = None,
    stage_metrics_ms: dict[str, list[float]] | None = None,
    worker_busy_skip_count: int | None = None,
    worker_submit_count: int | None = None,
    worker_result_count: int | None = None,
    source_resolution: list[int] | None = None,
    stream_resolution: list[int] | None = None,
    processing_resolution: list[int] | None = None,
    model_imgsz: int | None = None,
) -> dict[str, Any]:
    duration_sec = max(float(ended_at) - float(started_at), 1e-9)
    avg_fps = float(frame_count) / duration_sec
    avg_pose_confidence = (
        float(pose_confidence_sum) / float(pose_confidence_count)
        if pose_confidence_count > 0
        else 0.0
    )
    candidate_rate = float(candidate_count) / float(frame_count) if frame_count > 0 else 0.0
    resolved_inference_frame_count = int(inference_frame_count) if inference_frame_count is not None else int(frame_count)
    inference_fps = float(resolved_inference_frame_count) / duration_sec
    expected_frames = int(round(duration_sec * float(target_fps))) if target_fps and target_fps > 0 else 0
    dropped_frame_estimate = max(0, expected_frames - int(frame_count)) if expected_frames else 0
    drop_rate_estimate = float(dropped_frame_estimate) / float(expected_frames) if expected_frames else 0.0
    pose_latency_ms = pose_latency_ms or []
    loop_latency_ms = loop_latency_ms or []
    frame_interval_ms = frame_interval_ms or []
    row: dict[str, Any] = {
        "camera_id": camera_id,
        "recorded_at": utc_iso_now(),
        "window_duration_sec": round(duration_sec, 4),
        "frame_count": int(frame_count),
        "avg_fps": round(avg_fps, 4),
        "target_fps": round(float(target_fps), 4) if target_fps is not None else None,
        "dropped_frame_estimate": int(dropped_frame_estimate),
        "drop_rate_estimate": round(drop_rate_estimate, 4),
        "avg_pose_confidence": round(avg_pose_confidence, 4),
        "candidate_count": int(candidate_count),
        "candidate_rate": round(candidate_rate, 4),
        "inference_frame_count": resolved_inference_frame_count,
        "inference_fps": round(inference_fps, 4),
        "pose_inference_avg_ms": _round_mean(pose_latency_ms),
        "pose_inference_p95_ms": _round_p95(pose_latency_ms),
        "loop_avg_ms": _round_mean(loop_latency_ms),
        "loop_p95_ms": _round_p95(loop_latency_ms),
        "frame_interval_p95_ms": _round_p95(frame_interval_ms),
    }
    for key, values in (stage_metrics_ms or {}).items():
        row[key] = _round_mean(values)
        p95_key = key[:-3] + "_p95_ms" if key.endswith("_ms") else f"{key}_p95"
        row[p95_key] = _round_p95(values)
    if worker_busy_skip_count is not None:
        row["worker_busy_skip_count"] = int(worker_busy_skip_count)
    if worker_submit_count is not None:
        row["worker_submit_count"] = int(worker_submit_count)
    if worker_result_count is not None:
        row["worker_result_count"] = int(worker_result_count)
    if source_resolution is not None:
        row["source_resolution"] = [int(value) for value in source_resolution]
    if stream_resolution is not None:
        row["stream_resolution"] = [int(value) for value in stream_resolution]
    if processing_resolution is not None:
        row["processing_resolution"] = [int(value) for value in processing_resolution]
    if model_imgsz is not None:
        row["model_imgsz"] = int(model_imgsz)
    return row


def _round_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(sum(values)) / float(len(values)), 4)


def _round_p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered) + 0.999999) - 1))
    return round(ordered[index], 4)


def _build_video_ref(sequence_items: list[dict[str, Any]]) -> VideoRef | None:
    segment_ids: list[str] = []
    start_offset_ms = 0
    end_offset_ms = 0

    for index, item in enumerate(sequence_items):
        frame_ref = item.get("frame_ref") or {}
        segment_id = frame_ref.get("segment_id")
        if not segment_id:
            continue
        if not segment_ids:
            start_offset_ms = int(frame_ref.get("segment_offset_ms", 0))
        end_offset_ms = int(frame_ref.get("segment_offset_ms", 0))
        if segment_id not in segment_ids:
            segment_ids.append(str(segment_id))

    if not segment_ids:
        return None

    return VideoRef(
        segment_ids=segment_ids,
        start_offset_ms=start_offset_ms,
        end_offset_ms=end_offset_ms,
    )


def _build_candidate_window(
    config: dict[str, Any],
    track_id: int,
    candidate_type: str,
    category: str,
    condition: str,
    coarse_action: str,
    coarse_confidence: float,
    risk_label: str | None,
    risk_confidence: float | None,
    trigger_flags: list[str],
    preprocess_meta: dict[str, Any],
    sequence_items: list[dict[str, Any]],
) -> CandidateWindow | None:
    if not sequence_items:
        return None

    fps = int(config["camera"].get("fps", 10))
    frames = [
        CandidateWindowFrame(
            frame_idx=index,
            ts_ms=int(item["ts_ms"]),
            capture_ts=item.get("capture_ts"),
            analysis_ts=item.get("analysis_ts"),
            pose_conf_mean=float(item["pose_conf_mean"]),
            bbox_xyxy=[int(v) for v in item["bbox_xyxy"]],
            keypoints_17=[[float(v) for v in kp] for kp in item["keypoints_17"]],
            features=_normalize_feature_map(item["features"]),
            inference_timing={
                str(key): float(value)
                for key, value in dict(item.get("inference_timing", {})).items()
                if isinstance(value, (int, float))
            },
        )
        for index, item in enumerate(sequence_items)
    ]

    video_ref = _build_video_ref(sequence_items)
    candidate_clip_ref = None
    if category == "DANGER":
        candidate_clip_ref = CandidateClipRef(
            enabled=True,
            clip_id=f"cand_{config['camera']['camera_id']}_{track_id}_{frames[-1].ts_ms}",
        )
        video_ref = None
    elif category != "ABNORMAL":
        video_ref = None

    return CandidateWindow(
        device_id=config["camera"].get("device_id", config["camera"]["camera_id"]),
        camera_id=config["camera"]["camera_id"],
        track_id=track_id,
        candidate_type=candidate_type,
        candidate_category=category,
        composite_condition=condition,
        coarse_action=coarse_action,
        coarse_confidence=float(coarse_confidence),
        capture_ts=frames[0].capture_ts,
        analysis_ts=utc_iso_now(),
        inference_timing=frames[-1].inference_timing if frames else {},
        risk_label=risk_label,
        risk_confidence=float(risk_confidence) if risk_confidence is not None else None,
        window={
            "start_ts_ms": frames[0].ts_ms,
            "end_ts_ms": frames[-1].ts_ms,
            "fps": fps,
            "frame_count": len(frames),
        },
        trigger_flags=trigger_flags,
        preprocess=dict(preprocess_meta),
        sequence=frames,
        video_ref=video_ref,
        candidate_clip_ref=candidate_clip_ref,
    )


def _tier_drop_gate_passes(
    config: dict[str, Any],
    feature_map: dict[str, Any],
    action_label: str,
    trigger_flags: list[str],
    risk_confidence: float | None,
) -> bool:
    tier_cfg = config.get("tier_classification", {})
    gate_cfg = tier_cfg.get("fallback_gate", {})
    if not bool(gate_cfg.get("enabled", True)):
        return True

    dynamic_flags = {
        str(item)
        for item in gate_cfg.get(
            "dynamic_flags",
            [
                "torso_angle_spike",
                "vertical_velocity_spike",
                "center_velocity_spike",
                "bbox_aspect_ratio_change",
                "coarse_label_transition",
                "knee_angle_collapse",
            ],
        )
    }
    has_dynamic_flag = any(flag in dynamic_flags for flag in trigger_flags)
    if has_dynamic_flag:
        return True

    center_velocity = abs(float(feature_map.get("center_velocity_px_s", 0.0)))
    vertical_velocity = abs(float(feature_map.get("vertical_velocity_px_s", 0.0)))
    head_below_hip = "head_below_hip" in trigger_flags
    min_center_velocity = float(gate_cfg.get("min_center_velocity_px_s", 50.0))
    min_vertical_velocity = float(gate_cfg.get("min_abs_vertical_velocity_px_s", 50.0))
    require_lying = bool(gate_cfg.get("require_lying_label", True))

    if require_lying and action_label != "LYING":
        return False

    weak_motion = center_velocity >= min_center_velocity or vertical_velocity >= min_vertical_velocity
    if weak_motion or head_below_hip:
        return True

    min_high_confidence = gate_cfg.get("min_high_confidence")
    if min_high_confidence is not None and (risk_confidence or 0.0) >= float(min_high_confidence):
        return True

    return False


def build_pose_loss_fallback_skeleton_frames(
    *,
    camera_id: str,
    room_id: str | None,
    pose_model: str,
    people: list[dict[str, Any]],
    timestamp_ms: int,
    capture_ts: str | None,
    analysis_ts: str | None,
    age_ms: int,
    confidence_scale: float,
) -> list[SkeletonFrame]:
    frames: list[SkeletonFrame] = []
    bounded_scale = min(1.0, max(0.0, float(confidence_scale)))
    for person in people:
        detection = person["detection"]
        track_id = int(person["track_id"])
        feature_map = _normalize_feature_map(dict(person.get("feature_map", {})))
        feature_map["pose_lost_fallback"] = 1.0
        feature_map["pose_lost_duration_ms"] = float(max(0, int(age_ms)))
        feature_map["pose_confidence_mean"] = float(detection.pose_confidence_mean) * bounded_scale
        frames.append(
            SkeletonFrame(
                camera_id=camera_id,
                room_id=room_id,
                frame_id=f"{camera_id}-{timestamp_ms}-{track_id}-pose-lost",
                timestamp_ms=timestamp_ms,
                capture_ts=capture_ts,
                analysis_ts=analysis_ts,
                track_id=track_id,
                bbox=BoundingBox(
                    x1=int(detection.bbox[0]),
                    y1=int(detection.bbox[1]),
                    x2=int(detection.bbox[2]),
                    y2=int(detection.bbox[3]),
                ),
                bbox_confidence=max(0.0, min(1.0, float(detection.bbox_confidence) * bounded_scale)),
                keypoints=[
                    Keypoint(
                        x=float(kp[0]),
                        y=float(kp[1]),
                        confidence=max(0.0, min(1.0, float(kp[2]) * bounded_scale)),
                    )
                    for kp in detection.keypoints
                ],
                pose_confidence_mean=max(0.0, min(1.0, float(detection.pose_confidence_mean) * bounded_scale)),
                features=feature_map,
                risk={"raw_score": 0.0, "ema_score": 0.0, "pose_lost_fallback": True},
                event_state="NORMAL",
                pose_model=pose_model,
            )
        )
    return frames


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Edge action analysis pipeline")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--source-mode", choices=["auto", "device", "file", "url", "phone_usb"], default=None)
    parser.add_argument("--source", default=None, help="video source path/url/device index")
    parser.add_argument("--camera-backend", choices=["opencv", "picamera2"], default=None)
    parser.add_argument("--disable-server", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--run-seconds", type=int, default=0)
    return parser


def apply_cli_overrides(config: dict, args: argparse.Namespace) -> dict:
    if getattr(args, "camera_backend", None) is not None:
        config["camera"]["backend"] = args.camera_backend
    if args.source_mode is not None:
        config["camera"]["source_mode"] = args.source_mode
    if args.source is not None:
        source_value: str | int = args.source
        if args.source.isdigit() and (args.source_mode in {None, "auto", "device"}):
            source_value = int(args.source)
        config["camera"]["source"] = source_value
    if args.disable_server:
        config["server"]["enabled"] = False
    return config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    args = build_parser().parse_args()
    _load_project_dotenv()
    config = apply_cli_overrides(load_config(args.config), args)
    validate_runtime_security(config)
    apply_runtime_env_overrides(config)
    runtime_role = str(config.get("runtime", {}).get("role", "full_edge"))
    skeleton_sender_role = runtime_role == "skeleton_sender"

    camera = CameraSource(config)
    preprocessor = FramePreprocessor(config)
    pose_estimator = YoloPoseEstimator(config)
    tracker = SimpleTracker(
        max_age_ms=int(config["classification"].get("track_max_age_ms", 1500)),
        iou_threshold=float(config["classification"].get("track_iou_threshold", 0.25)),
    )
    feature_extractor = FeatureExtractor(config.get("room_rois", {}))
    classifier = None if skeleton_sender_role else ActionClassifier(config)
    tier_classifier = None if skeleton_sender_role else TierClassifier(config)
    trigger_engine = None if skeleton_sender_role else TriggerEngine(config)
    candidate_sender = None if skeleton_sender_role else CandidateSender(config)
    skeleton_sender = SkeletonWebSocketSender(config) if skeleton_sender_role else None
    timeline = TimelineBuilder(max_idle_ms=int(config["classification"].get("timeline_idle_ms", 3000)))
    client = ServerClient(config)
    buffer = RollingVideoBuffer(
        save_dir=config["buffer"]["save_dir"],
        fps=int(config["camera"]["fps"]),
        resolution=tuple(config["camera"]["resolution"]),
        segment_duration_sec=int(config["buffer"].get("segment_duration_sec", 3)),
        max_segments=config["buffer"].get("max_segments"),
        max_buffer_minutes=config["buffer"].get("max_buffer_minutes"),
        codec=config["buffer"].get("codec", "mp4v"),
        index_file=config["buffer"].get("index_file", "segments_index.jsonl"),
        write_enabled=bool(config["buffer"].get("write_enabled", True)),  # OPT-POSE-3
    )
    clip_manager = ClipManager(config, buffer, client)
    event_listener = EdgeEventListener(config, clip_manager.handle_clip_request)
    clip_request_cfg = config.get("clip_request_server", {})
    clip_rest_server = None
    if bool(clip_request_cfg.get("enabled", False)):
        clip_rest_server = ClipRestServer(
            host=str(clip_request_cfg.get("host", "0.0.0.0")),
            port=int(clip_request_cfg.get("port", 8091)),
            handler=ClipRequestHandler(clip_manager),
        )
    streamer = RTSPStreamer(config)
    local_output = LocalOutputWriter(config)
    sequence_window_frames = max(12, int(config["camera"].get("fps", 10)) * 2)
    candidate_cooldown_ms = int(config["classification"].get("candidate_cooldown_ms", 1000))
    sequence_buffers: dict[int, deque] = defaultdict(lambda: deque(maxlen=sequence_window_frames))
    last_candidate_sent_ms: dict[tuple[int, str], int] = {}

    camera.open()
    streamer.start()
    event_listener.start()
    if clip_rest_server is not None:
        clip_rest_server.start()
    LOGGER.info("camera source mode=%s source=%s", camera.source_mode, config["camera"].get("source"))

    try:
        client.register_camera(
            CameraRegistration(
                camera_id=config["camera"]["camera_id"],
                room_id=config["camera"].get("room_id"),
                display_name=config["camera"].get("display_name"),
                stream_url=config["camera"].get("stream_url"),
                metadata={
                    "pose_model": pose_estimator.model_name,
                    "classifier_model": classifier.model_name if classifier is not None else "none",
                    "runtime_role": runtime_role,
                },
            )
        )
    except Exception as exc:
        LOGGER.warning("camera register failed: %s", exc)

    frame_batch = []
    skeleton_batch = []
    segment_batch = []
    local_frame_batch = []
    local_candidate_batch = []
    local_trigger_debug_batch = []
    local_perf_stats_batch = []
    flush_interval = float(config["sender"].get("flush_interval_sec", 1.0))
    last_flush = time.time()
    started_at = time.time()
    processed_frames = 0
    perf_cfg = config.get("runtime_metrics", {})
    perf_interval_sec = float(
        perf_cfg.get(
            "perf_stats_interval_sec",
            config.get("local_output", {}).get("perf_stats_interval_sec", 30.0),
        )
    )
    perf_window_started_at = time.time()
    perf_frame_count = 0
    perf_pose_confidence_sum = 0.0
    perf_pose_confidence_count = 0
    perf_candidate_count = 0
    perf_inference_frame_count = 0
    perf_pose_latency_ms: list[float] = []
    perf_loop_latency_ms: list[float] = []
    perf_frame_interval_ms: list[float] = []
    perf_last_frame_started_at: float | None = None
    target_fps = float(config["camera"].get("fps", 0.0) or 0.0)
    inference_stride = max(1, int(config.get("model", {}).get("inference_stride", 1) or 1))
    pose_worker = LatestPoseInferenceWorker(_run_pose_inference)
    stream_overlay_max_age_ms = max(0, int(config.get("stream", {}).get("overlay_max_age_ms", 500) or 0))
    latest_stream_overlays: list[dict[str, Any]] = []
    latest_stream_overlay_ts_ms = 0
    sender_cfg = config.get("sender", {})
    pose_loss_fallback_enabled = bool(sender_cfg.get("pose_loss_fallback_enabled", True))
    pose_loss_fallback_frames = max(0, int(sender_cfg.get("pose_loss_fallback_frames", 15) or 0))
    pose_loss_fallback_max_age_ms = max(0, int(sender_cfg.get("pose_loss_fallback_max_age_ms", 900) or 0))
    pose_loss_fallback_confidence_scale = float(sender_cfg.get("pose_loss_fallback_confidence_scale", 0.5) or 0.0)
    last_skeleton_people: list[dict[str, Any]] = []
    last_skeleton_timestamp_ms = 0
    pose_loss_fallback_count = 0

    try:
        while True:
            loop_started_at = time.perf_counter()
            if args.run_seconds and time.time() - started_at >= args.run_seconds:
                LOGGER.info("run_seconds reached: %s", args.run_seconds)
                break
            if args.max_frames and processed_frames >= args.max_frames:
                LOGGER.info("max_frames reached: %s", args.max_frames)
                break

            packet = camera.read()
            if packet is None:
                if camera.exhausted:
                    LOGGER.info("input source exhausted")
                    break
                time.sleep(0.05)
                continue

            frame_started_at = time.perf_counter()
            if perf_last_frame_started_at is not None:
                perf_frame_interval_ms.append((frame_started_at - perf_last_frame_started_at) * 1000.0)
            perf_last_frame_started_at = frame_started_at
            capture_ts = utc_iso_from_ms(packet.timestamp_ms)
            buffer.write(packet.frame, packet.timestamp_ms)
            stream_packet = packet
            current_frame_ref = buffer.current_frame_ref(packet.timestamp_ms)

            pose_result = None
            try:
                pose_result = pose_worker.poll()
            except Exception as exc:
                LOGGER.warning("pose inference failed: %s", exc)

            run_inference = processed_frames % inference_stride == 0
            if run_inference:
                pose_worker.submit(
                    {
                        "packet": packet,
                        "frame_ref": current_frame_ref,
                        "capture_ts": capture_ts,
                    },
                    packet.frame.copy(),
                    config,
                    preprocessor,
                    pose_estimator,
                )

            if pose_result is None:
                frame_ref = current_frame_ref
                preprocess_metadata = {"inference_pending": True, "inference_stride": inference_stride}
                frame_for_inference = packet.frame
                detections = []
                track_ids = []
            else:
                inference_context = pose_result.context
                packet = inference_context["packet"]
                capture_ts = inference_context["capture_ts"]
                frame_ref = inference_context["frame_ref"]
                inference_output = pose_result.output
                preprocess_metadata = inference_output.preprocess_metadata
                frame_for_inference = inference_output.frame_for_inference
                detections = inference_output.detections
                perf_pose_latency_ms.append(float(inference_output.latency_ms))
                perf_inference_frame_count += 1
                track_ids = tracker.update([d.bbox for d in detections], packet.timestamp_ms)
            persons = []
            processed_people = []
            perf_frame_count += 1
            for detection in detections:
                perf_pose_confidence_sum += float(detection.pose_confidence_mean)
                perf_pose_confidence_count += 1

            for detection, track_id in zip(detections, track_ids):
                feature_map, feature_vector = feature_extractor.extract(
                    track_id=track_id,
                    bbox=detection.bbox,
                    keypoints=detection.keypoints,
                    frame_shape=frame_for_inference.shape,
                    timestamp_ms=packet.timestamp_ms,
                )
                action_start = time.perf_counter()
                if classifier is None:
                    action_label, action_confidence = "UNKNOWN", 0.0
                else:
                    action_label, action_confidence = classifier.predict(track_id, feature_map, feature_vector)
                action_ms = (time.perf_counter() - action_start) * 1000.0
                tier_start = time.perf_counter()
                if tier_classifier is None:
                    risk_label, risk_confidence = "NORMAL", 0.0
                else:
                    risk_label, risk_confidence = tier_classifier.update(track_id, feature_map)
                tier_ms = (time.perf_counter() - tier_start) * 1000.0
                analysis_ts = utc_iso_now()
                tier_enabled = bool(tier_classifier is not None and tier_classifier.enabled)
                label_source = "xgboost" if tier_enabled else ("skeleton_only" if skeleton_sender_role else "heuristic")
                xgboost_prob = float(risk_confidence) if tier_enabled else None
                xgboost_tier_ms = round(tier_ms, 4) if tier_enabled else None
                inference_timing = {
                    "action_classifier_ms": round(action_ms, 4),
                    "xgboost_tier_ms": round(tier_ms, 4),
                    "xgboost_total_ms": round(action_ms + tier_ms, 4),
                }
                feature_map = dict(feature_map)
                feature_map["xgboost_prob"] = float(xgboost_prob or 0.0)
                feature_map.update(inference_timing)
                processed_people.append(
                    {
                        "detection": detection,
                        "track_id": track_id,
                        "feature_map": feature_map,
                        "feature_vector": feature_vector,
                        "action_label": action_label,
                        "action_confidence": action_confidence,
                        "risk_label": risk_label,
                        "risk_confidence": risk_confidence,
                        "label_source": label_source,
                        "xgboost_prob": xgboost_prob,
                        "xgboost_tier_ms": xgboost_tier_ms,
                        "capture_ts": capture_ts,
                        "analysis_ts": analysis_ts,
                        "inference_timing": inference_timing,
                    }
                )
                persons.append(
                    PosePerson(
                        track_id=track_id,
                        bbox=BoundingBox(x1=detection.bbox[0], y1=detection.bbox[1], x2=detection.bbox[2], y2=detection.bbox[3]),
                        bbox_confidence=detection.bbox_confidence,
                        keypoints=[Keypoint(x=kp[0], y=kp[1], confidence=kp[2]) for kp in detection.keypoints],
                        pose_confidence_mean=detection.pose_confidence_mean,
                        features=feature_map,
                        action_label=action_label,
                        action_confidence=action_confidence,
                        risk_label=risk_label,
                        risk_confidence=risk_confidence if risk_confidence > 0 else None,
                        label_source=label_source,
                        xgboost_prob=xgboost_prob,
                        xgboost_tier_ms=xgboost_tier_ms,
                        pose_model=pose_estimator.model_name,
                        classifier_model=classifier.model_name if classifier is not None else "none",
                    )
                )
                if skeleton_sender_role:
                    skeleton_batch.append(
                        SkeletonFrame(
                            camera_id=config["camera"]["camera_id"],
                            room_id=config["camera"].get("room_id"),
                            frame_id=f"{config['camera']['camera_id']}-{packet.timestamp_ms}-{track_id}",
                            timestamp_ms=packet.timestamp_ms,
                            capture_ts=capture_ts,
                            analysis_ts=analysis_ts,
                            track_id=track_id,
                            bbox=BoundingBox(
                                x1=detection.bbox[0],
                                y1=detection.bbox[1],
                                x2=detection.bbox[2],
                                y2=detection.bbox[3],
                            ),
                            bbox_confidence=detection.bbox_confidence,
                            keypoints=[Keypoint(x=kp[0], y=kp[1], confidence=kp[2]) for kp in detection.keypoints],
                            pose_confidence_mean=detection.pose_confidence_mean,
                            features=feature_map,
                            risk={"raw_score": 0.0, "ema_score": 0.0},
                            event_state="NORMAL",
                            pose_model=pose_estimator.model_name,
                        )
                    )
                finished = timeline.update(
                    camera_id=config["camera"]["camera_id"],
                    room_id=config["camera"].get("room_id"),
                    track_id=track_id,
                    action_label=action_label,
                    action_confidence=action_confidence,
                    timestamp_ms=packet.timestamp_ms,
                    summary_features={
                        "torso_angle_deg": feature_map["torso_angle_deg"],
                        "bbox_aspect_ratio": feature_map["bbox_aspect_ratio"],
                        "center_velocity_px_s": feature_map["center_velocity_px_s"],
                    },
                )
                if finished is not None:
                    segment_batch.append(finished)

            if skeleton_sender_role:
                if processed_people:
                    last_skeleton_people = processed_people
                    last_skeleton_timestamp_ms = int(packet.timestamp_ms)
                    pose_loss_fallback_count = 0
                elif (
                    pose_loss_fallback_enabled
                    and last_skeleton_people
                    and pose_loss_fallback_count < pose_loss_fallback_frames
                ):
                    fallback_timestamp_ms = int(stream_packet.timestamp_ms)
                    fallback_age_ms = fallback_timestamp_ms - int(last_skeleton_timestamp_ms)
                    if 0 <= fallback_age_ms <= pose_loss_fallback_max_age_ms:
                        skeleton_batch.extend(
                            build_pose_loss_fallback_skeleton_frames(
                                camera_id=config["camera"]["camera_id"],
                                room_id=config["camera"].get("room_id"),
                                pose_model=pose_estimator.model_name,
                                people=last_skeleton_people,
                                timestamp_ms=fallback_timestamp_ms,
                                capture_ts=utc_iso_from_ms(fallback_timestamp_ms),
                                analysis_ts=utc_iso_now(),
                                age_ms=fallback_age_ms,
                                confidence_scale=pose_loss_fallback_confidence_scale,
                            )
                        )
                        pose_loss_fallback_count += 1

            if streamer.overlay_enabled and processed_people:
                latest_stream_overlays = processed_people
                latest_stream_overlay_ts_ms = packet.timestamp_ms

            if streamer.overlay_enabled:
                overlay_age_ms = stream_packet.timestamp_ms - latest_stream_overlay_ts_ms
                active_overlays = (
                    latest_stream_overlays
                    if latest_stream_overlays and 0 <= overlay_age_ms <= stream_overlay_max_age_ms
                    else []
                )
                streamer.write_frame(stream_packet.frame, active_overlays)
            else:
                streamer.write_frame(stream_packet.frame)

            segment_batch.extend(timeline.flush_stale(packet.timestamp_ms))
            if persons or bool(config["sender"].get("send_empty_frames", False)):
                frame_model = ActivityFrame(
                        camera_id=config["camera"]["camera_id"],
                        room_id=config["camera"].get("room_id"),
                        frame_id=f"{config['camera']['camera_id']}-{packet.timestamp_ms}",
                        timestamp_ms=packet.timestamp_ms,
                        capture_ts=capture_ts,
                        analysis_ts=utc_iso_now(),
                        persons=persons,
                )
                frame_batch.append(frame_model)
                local_frame_batch.append(
                    {
                        **frame_model.model_dump(mode="json"),
                        "source_mode": packet.source_mode,
                        "source_ref": packet.source_ref,
                        "video_ref": frame_ref,
                        "preprocess": preprocess_metadata,
                    }
                )

            for person in processed_people:
                if skeleton_sender_role or trigger_engine is None or candidate_sender is None:
                    continue
                detection = person["detection"]
                track_id = int(person["track_id"])
                feature_map = person["feature_map"]
                action_label = str(person["action_label"])
                action_confidence = float(person["action_confidence"])
                risk_label = str(person["risk_label"]) if person["risk_label"] is not None else None
                risk_confidence = float(person["risk_confidence"]) if person["risk_confidence"] is not None else None
                sequence_buffers[track_id].append(
                    {
                        "ts_ms": packet.timestamp_ms,
                        "capture_ts": person["capture_ts"],
                        "analysis_ts": person["analysis_ts"],
                        "pose_conf_mean": detection.pose_confidence_mean,
                        "bbox_xyxy": list(detection.bbox),
                        "keypoints_17": [list(kp) for kp in detection.keypoints[:17]],
                        "features": dict(feature_map),
                        "inference_timing": dict(person["inference_timing"]),
                        "frame_ref": frame_ref,
                    }
                )
                trigger_flags = trigger_engine.update(
                    track_id=track_id,
                    features=feature_map,
                    coarse_label=action_label,
                    ts_ms=packet.timestamp_ms,
                )
                if trigger_flags:
                    local_trigger_debug_batch.append(
                        {
                            "camera_id": config["camera"]["camera_id"],
                            "room_id": config["camera"].get("room_id"),
                            "track_id": track_id,
                            "timestamp_ms": packet.timestamp_ms,
                            "capture_ts": person["capture_ts"],
                            "analysis_ts": person["analysis_ts"],
                            "action_label": action_label,
                            "action_confidence": action_confidence,
                            "risk_label": risk_label,
                            "risk_confidence": risk_confidence,
                            "trigger_flags": trigger_flags,
                            "inference_timing": person["inference_timing"],
                            "frame_ref": frame_ref,
                        }
                    )
                judged = trigger_engine.judge_candidate(track_id, trigger_flags, action_label)
                if (
                    judged is None
                    and risk_label == "DROP"
                    and (risk_confidence or 0.0) >= float(config.get("tier_classification", {}).get("confidence_threshold", 0.55))
                    and _tier_drop_gate_passes(
                        config=config,
                        feature_map=feature_map,
                        action_label=action_label,
                        trigger_flags=trigger_flags,
                        risk_confidence=risk_confidence,
                    )
                ):
                    judged = (
                        "TIER_DROP_SUSPECT",
                        "ABNORMAL",
                        "XGBoost fall-binary high-confidence candidate",
                    )
                if judged is None:
                    continue
                candidate_type, category, condition = judged
                cooldown_key = (track_id, candidate_type)
                last_sent = last_candidate_sent_ms.get(cooldown_key, 0)
                if packet.timestamp_ms - last_sent < candidate_cooldown_ms:
                    continue

                window = _build_candidate_window(
                    config=config,
                    track_id=track_id,
                    candidate_type=candidate_type,
                    category=category,
                    condition=condition,
                    coarse_action=action_label,
                    coarse_confidence=action_confidence,
                    risk_label=risk_label,
                    risk_confidence=risk_confidence,
                    trigger_flags=trigger_flags,
                    preprocess_meta=preprocess_metadata,
                    sequence_items=list(sequence_buffers[track_id]),
                )
                if window is None:
                    continue
                try:
                    candidate_sender.send(window)
                    last_candidate_sent_ms[cooldown_key] = packet.timestamp_ms
                    local_candidate_batch.append(window.model_dump(mode="json"))
                    perf_candidate_count += 1
                except Exception as exc:
                    LOGGER.warning("candidate send failed: %s", exc)

            now = time.time()
            perf_loop_latency_ms.append((time.perf_counter() - loop_started_at) * 1000.0)
            if perf_interval_sec > 0 and now - perf_window_started_at >= perf_interval_sec:
                local_perf_stats_batch.append(
                    _build_perf_stats_row(
                        camera_id=config["camera"]["camera_id"],
                        started_at=perf_window_started_at,
                        ended_at=now,
                        frame_count=perf_frame_count,
                        pose_confidence_sum=perf_pose_confidence_sum,
                        pose_confidence_count=perf_pose_confidence_count,
                        candidate_count=perf_candidate_count,
                        target_fps=target_fps,
                        inference_frame_count=perf_inference_frame_count,
                        pose_latency_ms=perf_pose_latency_ms,
                        loop_latency_ms=perf_loop_latency_ms,
                        frame_interval_ms=perf_frame_interval_ms,
                    )
                )
                perf_window_started_at = now
                perf_frame_count = 0
                perf_pose_confidence_sum = 0.0
                perf_pose_confidence_count = 0
                perf_candidate_count = 0
                perf_inference_frame_count = 0
                perf_pose_latency_ms = []
                perf_loop_latency_ms = []
                perf_frame_interval_ms = []

            if time.time() - last_flush >= flush_interval:
                try:
                    if skeleton_sender_role and skeleton_sender is not None:
                        if skeleton_batch:
                            skeleton_sender.send_batch_sync(SkeletonFrameBatch(frames=skeleton_batch))
                    else:
                        client.send_activity_frames(ActivityFrameBatch(frames=frame_batch))
                        client.send_timeline_segments(TimelineBatch(segments=segment_batch))
                        if candidate_sender is not None:
                            candidate_sender.retry_pending()
                    clip_manager.retry_pending_uploads()
                except Exception as exc:
                    LOGGER.warning("flush failed: %s", exc)
                local_output.write_frames(local_frame_batch)
                local_output.write_timeline_segments([segment.model_dump(mode="json") for segment in segment_batch])
                local_output.write_candidates(local_candidate_batch)
                local_output.write_trigger_debug(local_trigger_debug_batch)
                local_output.write_perf_stats(local_perf_stats_batch)
                frame_batch.clear()
                skeleton_batch.clear()
                segment_batch.clear()
                local_frame_batch.clear()
                local_candidate_batch.clear()
                local_trigger_debug_batch.clear()
                local_perf_stats_batch.clear()
                last_flush = time.time()

            processed_frames += 1
    finally:
        segment_batch.extend(timeline.flush_all())
        if skeleton_sender_role and skeleton_sender is not None and skeleton_batch:
            try:
                skeleton_sender.send_batch_sync(SkeletonFrameBatch(frames=skeleton_batch))
                clip_manager.retry_pending_uploads()
            except Exception as exc:
                LOGGER.warning("final skeleton flush failed: %s", exc)
        local_output.write_frames(local_frame_batch)
        local_output.write_timeline_segments([segment.model_dump(mode="json") for segment in segment_batch])
        local_output.write_candidates(local_candidate_batch)
        local_output.write_trigger_debug(local_trigger_debug_batch)
        local_output.write_perf_stats(local_perf_stats_batch)
        pose_worker.shutdown(wait=False)
        camera.release()
        if clip_rest_server is not None:
            clip_rest_server.stop()
        streamer.stop()


if __name__ == "__main__":
    main()
