from __future__ import annotations

import unittest

from server.services.event_queue import EventQueue


class EventQueueTests(unittest.TestCase):
    def test_event_state_moves_from_suspicious_to_confirmed_and_resolved(self) -> None:
        queue = EventQueue(confirm_after_ms=1000, resolve_after_ms=1000)

        event = queue.update(
            camera_id="cam01",
            person_id="P001",
            event_type="fall_detected",
            risk_level="suspicious",
            risk_score=0.55,
            timestamp_ms=1000,
        )
        self.assertEqual(event.state, "SUSPICIOUS")

        event = queue.update(
            camera_id="cam01",
            person_id="P001",
            event_type="fall_detected",
            risk_level="danger",
            risk_score=0.9,
            timestamp_ms=1300,
        )
        self.assertEqual(event.state, "DANGEROUS")

        event = queue.update(
            camera_id="cam01",
            person_id="P001",
            event_type="fall_detected",
            risk_level="danger",
            risk_score=0.9,
            timestamp_ms=2400,
        )
        self.assertEqual(event.state, "CONFIRMED")

        event = queue.update(
            camera_id="cam01",
            person_id="P001",
            event_type="fall_detected",
            risk_level="normal",
            risk_score=0.1,
            timestamp_ms=4000,
        )
        self.assertEqual(event.state, "RESOLVED")


if __name__ == "__main__":
    unittest.main()
