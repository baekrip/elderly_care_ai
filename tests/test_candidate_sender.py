from __future__ import annotations

import unittest
from unittest.mock import Mock

from edge.candidate_sender import CandidateSender
from shared.protocol import CandidateWindow, CandidateWindowBatch


class CandidateSenderTests(unittest.TestCase):
    def test_skeleton_sender_role_disables_http_candidate_sender(self) -> None:
        sender = CandidateSender(
            {
                "runtime": {"role": "skeleton_sender"},
                "server": {"enabled": True, "base_url": "http://orin.local:8000"},
                "storage": {"pending_candidates_dir": "edge/storage/pending_candidates_test"},
            }
        )

        self.assertFalse(sender.enabled)

    def test_candidate_submit_uses_configured_ingest_api_key_header(self) -> None:
        sender = CandidateSender(
            {
                "camera": {"camera_id": "raspi_cam01"},
                "server": {
                    "enabled": True,
                    "base_url": "http://orin.local:8000",
                    "ingest_api_key": "edge-secret",
                    "ingest_api_key_header": "X-Edge-API-Key",
                    "require_device_id": True,
                },
                "storage": {"pending_candidates_dir": "edge/storage/pending_candidates_test"},
            }
        )
        response = Mock()
        response.json.return_value = {"accepted": 1}
        response.raise_for_status.return_value = None
        sender.session.post = Mock(return_value=response)

        batch = CandidateWindowBatch(
            windows=[
                CandidateWindow(
                    device_id="raspi_cam01",
                    camera_id="raspi_cam01",
                    track_id=1,
                    candidate_type="motion",
                    candidate_category="ABNORMAL",
                    composite_condition="running_over_speed",
                    coarse_action="running",
                    coarse_confidence=0.91,
                    window={"start_ts_ms": 1000, "end_ts_ms": 2000, "fps": 30, "frame_count": 30},
                    trigger_flags=["speed"],
                    sequence=[],
                    risk_label="ABNORMAL",
                    risk_confidence=0.7,
                )
            ]
        )

        sender.send_batch(batch)

        _, kwargs = sender.session.post.call_args
        self.assertEqual(kwargs["headers"]["X-Edge-API-Key"], "edge-secret")
        self.assertEqual(kwargs["headers"]["X-Device-ID"], "raspi_cam01")


if __name__ == "__main__":
    unittest.main()
