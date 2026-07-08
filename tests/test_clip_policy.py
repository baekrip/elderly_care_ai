from __future__ import annotations

import unittest

from edge.clip_policy import resolve_clip_window


class ClipPolicyTests(unittest.TestCase):
    def test_uses_pre_and_post_window_by_default(self) -> None:
        window = resolve_clip_window(
            center_ts_ms=1_000_000,
            pre_ms=90_000,
            post_ms=90_000,
        )

        self.assertEqual(window.start_ms, 910_000)
        self.assertEqual(window.end_ms, 1_090_000)
        self.assertEqual(window.duration_ms, 180_000)
        self.assertEqual(window.pre_ms, 90_000)
        self.assertEqual(window.post_ms, 90_000)

    def test_explicit_interval_is_used_for_server_confirmed_danger_window(self) -> None:
        window = resolve_clip_window(
            center_ts_ms=1_000_000,
            pre_ms=90_000,
            post_ms=90_000,
            explicit_start_ms=950_000,
            explicit_end_ms=1_120_000,
        )

        self.assertEqual(window.start_ms, 950_000)
        self.assertEqual(window.end_ms, 1_120_000)
        self.assertEqual(window.pre_ms, 50_000)
        self.assertEqual(window.post_ms, 120_000)

    def test_max_duration_clamps_window_around_event_time(self) -> None:
        window = resolve_clip_window(
            center_ts_ms=1_000_000,
            pre_ms=500_000,
            post_ms=500_000,
            max_duration_ms=600_000,
        )

        self.assertEqual(window.start_ms, 700_000)
        self.assertEqual(window.end_ms, 1_300_000)
        self.assertEqual(window.duration_ms, 600_000)


if __name__ == "__main__":
    unittest.main()
