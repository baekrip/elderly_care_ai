from __future__ import annotations

import unittest

from tools.post_sample_candidate import build_sample_candidate_batch


class SampleCandidateTests(unittest.TestCase):
    def test_build_sample_candidate_batch_uses_24_frames(self) -> None:
        payload = build_sample_candidate_batch(camera_id="cam01", device_id="pc01")
        self.assertEqual(len(payload["windows"]), 1)
        window = payload["windows"][0]
        self.assertEqual(window["candidate_category"], "DANGER")
        self.assertEqual(window["coarse_action"], "FALL")
        self.assertEqual(len(window["sequence"]), 24)

    def test_build_sample_candidate_batch_can_use_live_end_timestamp(self) -> None:
        payload = build_sample_candidate_batch(
            camera_id="cam01",
            device_id="pc01",
            end_ts_ms=1_780_000_000_000,
        )

        window = payload["windows"][0]
        self.assertEqual(window["window"]["end_ts_ms"], 1_780_000_000_000)
        self.assertEqual(window["sequence"][-1]["ts_ms"], 1_780_000_000_000)
        self.assertEqual(window["window"]["start_ts_ms"], window["sequence"][0]["ts_ms"])


if __name__ == "__main__":
    unittest.main()
