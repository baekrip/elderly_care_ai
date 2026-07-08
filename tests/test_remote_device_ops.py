from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.remote_device_ops import (
    CommandResult,
    DeviceTarget,
    RemoteDeviceOps,
    _start_orin_server_command,
    _start_pi5_edge_command,
    _start_mediamtx_command,
    _resolve_clip_request_api_key,
    build_scp_command,
    build_short_stability_validation_report,
    build_ssh_command,
    pi5_camera_contention_check,
    pi5_latest_clip_request,
    pi5_danger_e2e_probe,
    pi5_performance_window_check,
    pi5_skeleton_websocket_probe,
    build_stability_report,
    run_command,
)


class RemoteDeviceOpsTests(unittest.TestCase):
    def test_build_ssh_command_uses_batch_mode_and_timeout(self) -> None:
        target = DeviceTarget("pi5", "192.168.0.10", "eagleeye", "~/elderly_care_ai", Path("device_transfer/camera1"))

        command = build_ssh_command(target, "hostname", "C:/Users/me/.ssh/id_ed25519")

        self.assertEqual(command[:5], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.assertIn("-i", command)
        self.assertIn("C:/Users/me/.ssh/id_ed25519", command)
        self.assertEqual(command[-2:], ["eagleeye@192.168.0.10", "hostname"])

    def test_build_scp_command_copies_bundle_contents(self) -> None:
        target = DeviceTarget("orin", "jetson", "eagleeye", "~/elderly_care_ai", Path("device_transfer/Edge"))

        command = build_scp_command(target.bundle, target)

        self.assertEqual(command[0:2], ["scp", "-r"])
        self.assertEqual(command[-2], str(Path("device_transfer/Edge")) + os.sep + ".")
        self.assertEqual(command[-1], "eagleeye@jetson:~/elderly_care_ai/")

    def test_dry_run_check_writes_skipped_report(self) -> None:
        target = DeviceTarget("pi5", "pi5", "eagleeye", "~/elderly_care_ai", Path("device_transfer/camera1"))
        ops = RemoteDeviceOps(pi5=target, orin=None, dry_run=True)

        ops.check()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                from tools import remote_device_ops

                old_report_dir = remote_device_ops.REPORT_DIR
                remote_device_ops.REPORT_DIR = Path(tmpdir)
                report = ops.write_report("check")
                content = report.read_text(encoding="utf-8")
            finally:
                remote_device_ops.REPORT_DIR = old_report_dir

        self.assertIn('"dry_run": true', content)
        self.assertIn('"skipped": true', content)
        self.assertIn("pi5:camera_devices", content)

    def test_latest_clip_request_uses_segments_index_and_clip_endpoint(self) -> None:
        label, command, timeout_sec = pi5_latest_clip_request("~/elderly_care_ai", 8091)

        self.assertEqual(label, "latest_clip_request")
        self.assertIn("segments_index.jsonl", command)
        self.assertIn("http://127.0.0.1:8091/clip/request", command)
        self.assertEqual(timeout_sec, 90)

    def test_latest_clip_request_skips_malformed_index_rows(self) -> None:
        _, command, _ = pi5_latest_clip_request("~/elderly_care_ai", 8091)

        self.assertIn("except json.JSONDecodeError:", command)
        self.assertIn("continue", command)

    def test_latest_clip_request_can_target_bound_runtime_host(self) -> None:
        _, command, _ = pi5_latest_clip_request("~/elderly_care_ai", 8091, host="192.168.45.29")

        self.assertIn("http://192.168.45.29:8091/clip/request", command)

    def test_latest_clip_request_sends_edge_clip_request_api_key(self) -> None:
        _, command, _ = pi5_latest_clip_request(
            "~/elderly_care_ai",
            8091,
            clip_request_api_key="clip_request_live_test_key",
        )

        self.assertIn("clip_request_api_key = 'clip_request_live_test_key'", command)
        self.assertIn("clip_request_api_key_header = 'X-Edge-Clip-Key'", command)
        self.assertIn('f"{clip_request_api_key_header}: {clip_request_api_key}"', command)

    def test_clip_request_key_does_not_fallback_to_clip_upload_key(self) -> None:
        self.assertEqual(
            _resolve_clip_request_api_key("", "clip_upload_live_test_key"),
            "",
        )

    def test_patch_bundle_configs_records_runtime_hosts_in_dry_run(self) -> None:
        pi5 = DeviceTarget("pi5", "pi5", "eagleeye", "~/elderly_care_ai", Path("device_transfer/camera1"))
        orin = DeviceTarget("orin", "jetson", "eagleeye", "~/elderly_care_ai", Path("device_transfer/Edge"))
        ops = RemoteDeviceOps(
            pi5=pi5,
            orin=orin,
            pi_runtime_host="192.168.45.29",
            orin_runtime_host="192.168.45.241",
            dry_run=True,
        )

        ops.patch_bundle_configs()

        self.assertEqual(ops.results[-1].label, "local:patch_bundle_configs")
        self.assertIn("192.168.45.29", ops.results[-1].stdout)
        self.assertIn("192.168.45.241", ops.results[-1].stdout)

    def test_skeleton_websocket_probe_targets_orin_runtime_host(self) -> None:
        label, command, timeout_sec = pi5_skeleton_websocket_probe(
            "~/elderly_care_ai",
            "192.168.45.241",
            ingest_api_key="remote_device_ops_live_test_key",
        )

        self.assertEqual(label, "skeleton_ws_probe")
        self.assertIn("ws://192.168.45.241:8000/ws/skeleton/raspi_cam01", command)
        self.assertIn("remote_device_ops_probe", command)
        self.assertIn("API_KEY = 'remote_device_ops_live_test_key'", command)
        self.assertIn('"X-Edge-API-Key": _dotenv_key()', command)
        self.assertIn('"X-Device-ID": \'raspi_cam01\'', command)
        self.assertIn('"frames": frames', command)
        self.assertIn("for offset in range(3)", command)
        self.assertIn(".venv_edge/bin/python", command)
        self.assertEqual(timeout_sec, 30)

    def test_danger_e2e_probe_requires_danger_event_from_orin_pipeline(self) -> None:
        label, command, timeout_sec = pi5_danger_e2e_probe(
            "~/elderly_care_ai",
            "192.168.45.241",
            ingest_api_key="remote_device_ops_live_test_key",
        )

        self.assertEqual(label, "danger_e2e_probe")
        self.assertIn("ws://192.168.45.241:8000/ws/skeleton/raspi_cam01", command)
        self.assertIn("remote_device_ops_danger", command)
        self.assertIn("API_KEY = 'remote_device_ops_live_test_key'", command)
        self.assertIn('"X-Edge-API-Key": _dotenv_key()', command)
        self.assertIn('"X-Device-ID": \'raspi_cam01\'', command)
        self.assertIn('"center_velocity_px_s": 320.0', command)
        self.assertIn("event_count", command)
        self.assertIn("risk_label", command)
        self.assertEqual(timeout_sec, 45)

    def test_service_start_commands_can_inject_ingest_api_key(self) -> None:
        orin_command = _start_orin_server_command(
            "~/elderly_care_ai",
            ingest_api_key="remote_device_ops_live_test_key",
            clip_upload_api_key="clip_upload_live_test_key",
        )
        pi5_command = _start_pi5_edge_command(
            "~/elderly_care_ai",
            clip_server_host="192.168.45.29",
            ingest_api_key="remote_device_ops_live_test_key",
            clip_upload_api_key="clip_upload_live_test_key",
            clip_request_api_key="clip_request_live_test_key",
        )

        self.assertIn("env EDGE_INGEST_API_KEY=remote_device_ops_live_test_key", orin_command)
        self.assertIn("CLIP_UPLOAD_API_KEY=clip_upload_live_test_key", orin_command)
        self.assertIn("env EDGE_INGEST_API_KEY=remote_device_ops_live_test_key", pi5_command)
        self.assertIn("CLIP_UPLOAD_API_KEY=clip_upload_live_test_key", pi5_command)
        self.assertIn("EDGE_CLIP_REQUEST_API_KEY=clip_request_live_test_key", pi5_command)
        self.assertIn("host='\"'\"'192.168.45.29'\"'\"'", pi5_command)

    def test_service_start_commands_do_not_force_default_keys_without_manual_input(self) -> None:
        orin_command = _start_orin_server_command("~/elderly_care_ai")
        pi5_command = _start_pi5_edge_command("~/elderly_care_ai", clip_server_host="192.168.45.29")

        self.assertNotIn("elderly-care-local-ingest-key", orin_command)
        self.assertNotIn("elderly-care-local-clip-upload-key", orin_command)
        self.assertNotIn("elderly-care-local-ingest-key", pi5_command)
        self.assertNotIn("elderly-care-local-clip-upload-key", pi5_command)

    def test_camera_contention_check_rejects_v4l2_or_busy_errors(self) -> None:
        label, command, timeout_sec = pi5_camera_contention_check("~/elderly_care_ai")

        self.assertEqual(label, "camera_contention_check")
        self.assertIn("pi5_edge.log", command)
        self.assertIn("Device or resource busy", command)
        self.assertIn("v4l2", command)
        self.assertIn("rpicam-vid", command)
        self.assertEqual(timeout_sec, 30)

    def test_performance_window_check_aggregates_fresh_perf_rows(self) -> None:
        label, command, timeout_sec = pi5_performance_window_check(
            "~/elderly_care_ai",
            duration_sec=600,
            min_avg_fps=5.0,
            require_pose=False,
        )

        self.assertEqual(label, "performance_window_check")
        self.assertIn("perf_stats.jsonl", command)
        self.assertIn("daily", command)
        self.assertIn('strftime("%Y-%m-%d")', command)
        self.assertIn("duration_sec = 600", command)
        self.assertIn("min_avg_fps = 5.0", command)
        self.assertIn("avg_fps", command)
        self.assertEqual(timeout_sec, 690)

    def test_performance_window_check_can_use_explicit_fps_tolerance(self) -> None:
        _, command, _ = pi5_performance_window_check(
            "~/elderly_care_ai",
            duration_sec=60,
            min_avg_fps=30.0,
            fps_tolerance=0.05,
            require_pose=False,
        )

        self.assertIn("fps_tolerance = 0.05", command)
        self.assertIn('"fps_tolerance": fps_tolerance', command)
        self.assertIn("avg_fps + fps_tolerance < min_avg_fps", command)

    def test_start_mediamtx_checks_project_path_then_home_dir_binary(self) -> None:
        command = _start_mediamtx_command("~/elderly_care_ai")

        self.assertIn("[ -x ./mediamtx ] && [ -f ./mediamtx.yml ]", command)
        self.assertIn("command -v mediamtx", command)
        self.assertIn("[ -x ~/mediamtx/mediamtx ] && [ -f ~/mediamtx/mediamtx.yml ]", command)
        self.assertIn("cd ~/mediamtx && nohup ./mediamtx ./mediamtx.yml", command)
        self.assertIn("< /dev/null", command)

    def test_run_command_records_timeout_as_result(self) -> None:
        with patch(
            "tools.remote_device_ops.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["ssh", "host"], timeout=3),
        ):
            result = run_command("remote:hang", ["ssh", "host"], timeout_sec=3)

        self.assertIsInstance(result, CommandResult)
        self.assertEqual(result.label, "remote:hang")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out after 3", result.stderr)

    def test_build_stability_report_schema_flags_threshold_failures(self) -> None:
        results = [
            CommandResult(
                label="pi5:performance_window_check",
                command=[],
                returncode=0,
                stdout='{"avg_fps": 19.5, "drop_rate_mean": 0.2, "pose_inference_p95_ms_max": 120.0}',
                stderr="",
            ),
            CommandResult(
                label="orin:wait_orin_health",
                command=[],
                returncode=7,
                stdout="",
                stderr="connection failed",
            ),
        ]

        report = build_stability_report(results, duration_sec=3600)

        self.assertEqual(report["duration_sec"], 3600)
        self.assertEqual(report["avg_fps"], 19.5)
        self.assertEqual(report["orin_health_ok_rate"], 0.0)
        self.assertEqual(report["failure_thresholds"]["fps_below_threshold"], 20.0)
        self.assertTrue(any(item["type"] == "fps_below_threshold" for item in report["failure_events"]))
        self.assertTrue(any(item["type"] == "command_failed" for item in report["failure_events"]))

    def test_short_stability_validation_never_claims_long_run_pass(self) -> None:
        report = build_short_stability_validation_report(
            results=[],
            requested_duration_sec=600,
            sample_window_sec=60,
            dry_run=True,
        )

        self.assertTrue(report["tool_validation_passed"])
        self.assertFalse(report["long_run_passed"])
        self.assertEqual(report["requested_duration_sec"], 600)
        self.assertEqual(report["sample_window_sec"], 60)
        self.assertIn("wall_clock_started_at", report)
        self.assertIn("wall_clock_finished_at", report)
        self.assertEqual(report["cleanup_receipt"]["process_shutdown_required"], False)
        self.assertTrue(report["synthetic_probe_results"][0]["test_flag"])


if __name__ == "__main__":
    unittest.main()
