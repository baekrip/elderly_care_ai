from __future__ import annotations

import os
import re
import unittest

from server.services.backend_forwarder import (
    BackendConfig,
    BackendEventBatchScheduler,
    BackendResponse,
    PendingReplayResult,
    build_backend_event,
    build_candidate_backend_event,
    build_events_batch,
    build_immediate_alert,
    build_timeline_pattern_backend_event,
    config_from_project_config,
    extract_first_alert_id,
    extract_first_event_id,
    is_retryable_response,
    load_project_dotenv,
    map_event_type,
    retry_pending,
    should_forward_level,
    utc_iso_from_ms,
)


class BackendForwarderTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("BACKEND_BASE_URL", None)
        os.environ.pop("AI_BACKEND_BASE_URL", None)
        os.environ.pop("AI2_SERVER_URL", None)
        os.environ.pop("APP_TOKEN", None)
        os.environ.pop("BACKEND_APP_TOKEN", None)
        os.environ.pop("DOTENV_ONLY_KEY", None)
        os.environ.pop("DOTENV_KEEP_KEY", None)

    def test_config_uses_batch_and_alert_paths_from_project_config(self) -> None:
        config = config_from_project_config(
            {
                "backend": {
                    "enabled": True,
                    "base_url": "http://13.209.89.107:5000",
                    "token": "test-app-token",
                    "events_batch_path": "/api/v1/events/batch",
                    "alerts_immediate_path": "/api/v1/alerts/immediate",
                    "normal_batch_interval_sec": 300,
                    "request_timeout_sec": 7,
                    "pending_file": "server/storage/results/backend_pending.jsonl",
                }
            }
        )

        self.assertEqual(
            config,
            BackendConfig(
                enabled=True,
                base_url="http://13.209.89.107:5000",
                token="test-app-token",
                events_batch_path="/api/v1/events/batch",
                alerts_immediate_path="/api/v1/alerts/immediate",
                batch_interval_sec=10.0,
                normal_batch_interval_sec=300.0,
                request_timeout_sec=7.0,
                pending_file="server/storage/results/backend_pending.jsonl",
            ),
        )

    def test_config_uses_env_secret_overrides(self) -> None:
        os.environ["BACKEND_BASE_URL"] = "http://backend.example:5000"
        os.environ["APP_TOKEN"] = "runtime-token"

        config = config_from_project_config(
            {
                "backend": {
                    "enabled": True,
                    "base_url": "",
                    "token": "",
                }
            }
        )

        self.assertEqual(config.base_url, "http://backend.example:5000")
        self.assertEqual(config.token, "runtime-token")
        self.assertTrue(config.enabled)

    def test_config_accepts_ai2_server_url_env_alias(self) -> None:
        os.environ["AI2_SERVER_URL"] = "http://ai2-backend.example:5000"
        os.environ["APP_TOKEN"] = "runtime-token"

        config = config_from_project_config(
            {
                "backend": {
                    "enabled": True,
                    "base_url": "",
                    "token": "",
                }
            }
        )

        self.assertEqual(config.base_url, "http://ai2-backend.example:5000")
        self.assertEqual(config.token, "runtime-token")
        self.assertTrue(config.enabled)

    def test_load_project_dotenv_does_not_override_existing_env(self) -> None:
        with self.subTest("dotenv loader"):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                dotenv = Path(tmpdir) / ".env"
                dotenv.write_text(
                    "\n".join(
                        [
                            "DOTENV_ONLY_KEY=from-dotenv",
                            "DOTENV_KEEP_KEY=from-dotenv",
                        ]
                    ),
                    encoding="utf-8",
                )
                os.environ["DOTENV_KEEP_KEY"] = "from-shell"

                load_project_dotenv(dotenv)

        self.assertEqual(os.environ["DOTENV_ONLY_KEY"], "from-dotenv")
        self.assertEqual(os.environ["DOTENV_KEEP_KEY"], "from-shell")

    def test_config_disables_backend_when_enabled_but_base_url_missing(self) -> None:
        config = config_from_project_config(
            {
                "backend": {
                    "enabled": True,
                    "base_url": "",
                    "token": "",
                }
            }
        )

        self.assertFalse(config.enabled)
        self.assertEqual(config.base_url, "")

    def test_build_timeline_pattern_backend_event_uses_events_batch_shape(self) -> None:
        event = build_timeline_pattern_backend_event(
            device_key="raspi_cam01",
            patient_id="P001",
            window_start_ms=1_714_838_100_000,
            window_end_ms=1_714_838_400_000,
            segments=[
                {
                    "camera_id": "raspi_cam01",
                    "action_label": "WALKING",
                    "duration_ms": 60_000,
                }
            ],
            anomalies=[],
        )

        self.assertEqual(event["device_key"], "raspi_cam01")
        self.assertEqual(event["patient_id"], "P001")
        self.assertEqual(event["event_type"], "normal_activity_summary")
        self.assertEqual(event["severity"], 0)
        self.assertEqual(event["ts"], "2024-05-04T16:00:00.000Z")
        self.assertEqual(event["payload"]["payload_kind"], "timeline_pattern_batch")
        self.assertEqual(event["payload"]["delivery_policy"], "five_min_batch")
        self.assertEqual(event["payload"]["segments"][0]["action_label"], "WALKING")

    def test_build_timeline_pattern_backend_event_marks_pattern_anomaly(self) -> None:
        event = build_timeline_pattern_backend_event(
            device_key="raspi_cam01",
            patient_id="P001",
            window_start_ms=1_714_838_100_000,
            window_end_ms=1_714_838_400_000,
            segments=[],
            anomalies=[{"type": "PROLONGED_INACTIVITY", "level": "ABNORMAL"}],
        )

        self.assertEqual(event["event_type"], "pattern_anomaly_detected")
        self.assertEqual(event["severity"], 60)
        self.assertEqual(event["payload"]["delivery_policy"], "five_min_batch_force_on_anomaly")

    def test_map_internal_labels_to_backend_event_types(self) -> None:
        self.assertEqual(map_event_type("DANGER_DROP"), "fall_detected")
        self.assertEqual(map_event_type("GRADUAL_FALL"), "fall_detected")
        self.assertEqual(map_event_type("PROLONGED_FLOOR_LYING"), "lying_down")
        self.assertEqual(map_event_type("PROLONGED_INACTIVITY"), "no_movement")
        self.assertEqual(map_event_type("LOW_SKELETON_QUALITY"), "abnormal_posture")

    def test_should_forward_operational_levels(self) -> None:
        self.assertTrue(should_forward_level("normal"))
        self.assertTrue(should_forward_level("abnormal"))
        self.assertTrue(should_forward_level("danger"))
        self.assertFalse(should_forward_level("quality"))
        self.assertFalse(should_forward_level(None))

    def test_utc_iso_from_epoch_ms_uses_z_suffix(self) -> None:
        self.assertEqual(utc_iso_from_ms(1_714_838_400_000), "2024-05-04T16:00:00.000Z")

    def test_build_backend_event_uses_required_backend_fields(self) -> None:
        event = build_backend_event(
            device_key="cam_livingroom_01",
            patient_id="P001",
            source_label="DANGER_DROP",
            confidence=0.91,
            severity=85,
            timestamp_ms=1_714_838_400_000,
            payload={"duration_ms": 1500},
        )

        self.assertEqual(event["device_key"], "cam_livingroom_01")
        self.assertEqual(event["patient_id"], "P001")
        self.assertEqual(event["event_type"], "fall_detected")
        self.assertEqual(event["confidence"], 0.91)
        self.assertEqual(event["severity"], 85)
        self.assertEqual(event["ts"], "2024-05-04T16:00:00.000Z")
        self.assertEqual(event["payload"]["source_label"], "DANGER_DROP")
        self.assertEqual(event["payload"]["duration_ms"], 1500)
        self.assertRegex(str(event["payload"]["dedup_key"]), r"cam_livingroom_01:P001:.*:fall_detected")

    def test_batch_and_alert_payload_shapes(self) -> None:
        event = build_backend_event(
            device_key="cam_livingroom_01",
            patient_id="P001",
            event_type="fall_detected",
            confidence=0.91,
            severity=85,
            timestamp_ms=1_714_838_400_000,
            payload={"source": "unit-test"},
        )
        batch = build_events_batch([event])

        self.assertEqual(batch, {"events": [event]})

        alert = build_immediate_alert(
            device_key="cam_livingroom_01",
            patient_id="P001",
            alert_type="fall_detected",
            alert_level="danger",
            message="fall_detected danger sample",
            ts=event["ts"],
            ref_event_id=123,
            payload={"event_type": "fall_detected"},
        )

        self.assertEqual(alert["device_key"], "cam_livingroom_01")
        self.assertEqual(alert["patient_id"], "P001")
        self.assertEqual(alert["alert_type"], "fall_detected")
        self.assertEqual(alert["alert_level"], "danger")
        self.assertEqual(alert["level"], "danger")
        self.assertEqual(alert["ref_event_id"], 123)
        self.assertEqual(alert["payload"]["event_type"], "fall_detected")

    def test_build_candidate_backend_event_uses_effective_level_policy(self) -> None:
        event = build_candidate_backend_event(
            device_key="cam_livingroom_01",
            patient_id="P001",
            candidate_type="TIER_DROP_SUSPECT",
            candidate_category="ABNORMAL",
            final_label="DROP",
            effective_level="danger",
            confidence=0.83,
            timestamp_ms=1_714_838_400_000,
            capture_ts="2024-05-04T16:00:00.000Z",
            analysis_ts="2024-05-04T16:00:00.040Z",
            trigger_flags=["torso_angle_spike"],
            stgcn_inference_ms=20.5,
            frame_id=12345,
        )

        self.assertEqual(event["event_type"], "fall_detected")
        self.assertEqual(event["severity"], 90)
        self.assertEqual(event["ts"], "2024-05-04T16:00:00.000Z")
        self.assertEqual(event["frame_id"], 12345)
        self.assertEqual(event["payload"]["effective_level"], "danger")
        self.assertEqual(event["payload"]["analysis_ts"], "2024-05-04T16:00:00.040Z")
        self.assertEqual(event["payload"]["stgcn_inference_ms"], 20.5)

    def test_build_candidate_backend_event_preserves_normal_source_label(self) -> None:
        event = build_candidate_backend_event(
            device_key="cam_livingroom_01",
            patient_id="P001",
            candidate_type="DANGER_DROP",
            candidate_category="DANGER",
            final_label="NORMAL",
            effective_level="normal",
            confidence=0.9,
            timestamp_ms=1_714_838_400_000,
            trigger_flags=["FALL_DETECTED"],
        )

        self.assertEqual(event["payload"]["source_label"], "NORMAL")
        self.assertEqual(event["event_type"], "normal_activity_summary")
        self.assertEqual(event["severity"], 0)

    def test_map_event_type_normal_wins_over_fall_flags(self) -> None:
        self.assertEqual(
            map_event_type("NORMAL", ["FALL_DETECTED"]),
            "normal_activity_summary",
        )

    def test_extract_first_event_id_from_batch_response(self) -> None:
        response = {"results": [{"event_id": 123, "stored": True}], "count": 1}

        self.assertEqual(extract_first_event_id(response), 123)
        self.assertIsNone(extract_first_event_id({"results": []}))
        self.assertIsNone(extract_first_event_id(None))

    def test_extract_first_alert_id_from_immediate_alert_response(self) -> None:
        self.assertEqual(extract_first_alert_id({"alert_id": 9}), 9)
        self.assertEqual(extract_first_alert_id({"data": {"id": 10}}), 10)
        self.assertIsNone(extract_first_alert_id({"data": {}}))
        self.assertIsNone(extract_first_alert_id(None))

    def test_timestamp_for_relative_ms_falls_back_to_current_utc_shape(self) -> None:
        ts = utc_iso_from_ms(12_345)

        self.assertRegex(ts, re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"))

    def test_retry_pending_rewrites_only_failed_records(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "backend_pending.jsonl"
            records = [
                {"kind": "events_batch", "url": "http://backend/events", "payload": {"events": [{"id": 1}]}},
                {"kind": "alerts_immediate", "url": "http://backend/alerts", "payload": {"id": 2}},
            ]
            pending_file.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
                encoding="utf-8",
            )

            responses = [
                BackendResponse(ok=True, status_code=201, text="ok"),
                BackendResponse(ok=False, status_code=500, text="fail"),
            ]

            def post_json(url: str, payload: dict) -> BackendResponse:
                self.assertEqual(url, records[len(responses) - 2]["url"] if len(responses) == 2 else records[1]["url"])
                return responses.pop(0)

            result = retry_pending(pending_file, post_json=post_json)

            self.assertEqual(result, PendingReplayResult(attempted=2, sent=1, kept=1))
            remaining = [json.loads(line) for line in pending_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["kind"], "alerts_immediate")

    def test_retry_policy_only_retries_network_and_5xx_failures(self) -> None:
        self.assertTrue(is_retryable_response(BackendResponse(ok=False, status_code=0, text="timeout")))
        self.assertTrue(is_retryable_response(BackendResponse(ok=False, status_code=500, text="server error")))
        self.assertFalse(is_retryable_response(BackendResponse(ok=False, status_code=400, text="bad request")))
        self.assertFalse(is_retryable_response(BackendResponse(ok=True, status_code=201, text="ok")))

    def test_pending_policy_drops_oldest_rows_when_limit_is_exceeded(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from server.services.backend_forwarder import append_pending

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "backend_pending.jsonl"
            response = BackendResponse(ok=False, status_code=500, text="server error")

            for index in range(3):
                append_pending(
                    pending_file,
                    kind="events_batch",
                    url="http://backend/events",
                    payload={"events": [{"id": index}]},
                    response=response,
                    max_records=2,
                )

            rows = [json.loads(line) for line in pending_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["payload"]["events"][0]["id"] for row in rows], [1, 2])

    def test_scheduler_writes_contract_error_report_for_4xx(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "backend_pending.jsonl"
            contract_error_file = Path(tmpdir) / "backend_contract_errors.jsonl"

            class FakeForwarder:
                config = BackendConfig(
                    enabled=True,
                    base_url="http://backend",
                    token="token",
                    pending_file=str(pending_file),
                    contract_error_file=str(contract_error_file),
                )

                def post_events_batch(self, events):
                    return BackendResponse(ok=False, status_code=400, text="bad payload")

                def retry_pending(self) -> PendingReplayResult:
                    return PendingReplayResult()

            scheduler = BackendEventBatchScheduler(FakeForwarder(), interval_sec=0)
            result = scheduler.submit({"event_type": "abnormal_posture", "id": 1}, force=True)

            self.assertEqual(result.response.status_code, 400)
            self.assertFalse(pending_file.exists())
            rows = [json.loads(line) for line in contract_error_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["response_status"], 400)
            self.assertEqual(rows[0]["kind"], "events_batch")

    def test_backend_event_batch_scheduler_flushes_after_interval(self) -> None:
        posted_batches: list[list[dict]] = []
        responses = [BackendResponse(ok=True, status_code=201, text="ok")]

        class _Forwarder:
            config = BackendConfig()

            def post_events_batch(self, events: list[dict]) -> BackendResponse:
                posted_batches.append(list(events))
                return responses.pop(0)

            def retry_pending(self) -> PendingReplayResult:
                return PendingReplayResult()

        clock = {"now": 100.0}
        scheduler = BackendEventBatchScheduler(
            forwarder=_Forwarder(),
            interval_sec=10.0,
            now_func=lambda: clock["now"],
        )

        first = scheduler.submit({"event_type": "abnormal_posture", "id": 1})
        self.assertIsNone(first.response)
        self.assertEqual(first.queued, 1)
        self.assertEqual(posted_batches, [])

        clock["now"] = 109.0
        second = scheduler.submit({"event_type": "abnormal_posture", "id": 2})
        self.assertIsNone(second.response)
        self.assertEqual(second.queued, 2)

        clock["now"] = 111.0
        third = scheduler.submit({"event_type": "abnormal_posture", "id": 3})

        self.assertTrue(third.response.ok)
        self.assertEqual(third.forwarded, 3)
        self.assertEqual(posted_batches, [[
            {"event_type": "abnormal_posture", "id": 1},
            {"event_type": "abnormal_posture", "id": 2},
            {"event_type": "abnormal_posture", "id": 3},
        ]])

    def test_backend_event_batch_scheduler_keeps_failed_events_for_retry(self) -> None:
        responses = [
            BackendResponse(ok=False, status_code=500, text="fail"),
            BackendResponse(ok=True, status_code=201, text="ok"),
        ]
        posted_batches: list[list[dict]] = []

        class _Forwarder:
            config = BackendConfig()

            def post_events_batch(self, events: list[dict]) -> BackendResponse:
                posted_batches.append(list(events))
                return responses.pop(0)

            def retry_pending(self) -> PendingReplayResult:
                return PendingReplayResult()

        scheduler = BackendEventBatchScheduler(
            forwarder=_Forwarder(),
            interval_sec=10.0,
            now_func=lambda: 100.0,
        )

        failed = scheduler.submit({"event_type": "abnormal_posture", "id": 1}, force=True)
        self.assertFalse(failed.response.ok)
        self.assertEqual(failed.queued, 1)

        retried = scheduler.submit({"event_type": "abnormal_posture", "id": 2}, force=True)
        self.assertTrue(retried.response.ok)
        self.assertEqual(retried.forwarded, 2)
        self.assertEqual(posted_batches[-1], [
            {"event_type": "abnormal_posture", "id": 1},
            {"event_type": "abnormal_posture", "id": 2},
        ])


if __name__ == "__main__":
    unittest.main()
