from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ServerMainTests(unittest.TestCase):
    def test_import_server_main_has_no_eager_database_side_effect(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import server.main; print('server-main-import-ok')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("server-main-import-ok", result.stdout)

    def test_flush_batcher_if_pending_only_flushes_when_events_exist(self) -> None:
        from server.main import flush_batcher_if_pending

        class _Batcher:
            def __init__(self, events: list[dict]) -> None:
                self._events = events
                self.flush_count = 0

            def flush(self) -> str:
                self.flush_count += 1
                return "flushed"

        empty = _Batcher([])
        pending = _Batcher([{"event_type": "normal_activity_summary"}])

        self.assertIsNone(flush_batcher_if_pending(empty))
        self.assertEqual(empty.flush_count, 0)
        self.assertEqual(flush_batcher_if_pending(pending), "flushed")
        self.assertEqual(pending.flush_count, 1)


if __name__ == "__main__":
    unittest.main()
