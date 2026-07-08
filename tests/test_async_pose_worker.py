from __future__ import annotations

import threading
import unittest

from edge.async_pose import LatestPoseInferenceWorker


class AsyncPoseWorkerTests(unittest.TestCase):
    def test_submit_returns_false_while_worker_is_busy(self) -> None:
        release = threading.Event()

        def infer(value: int) -> int:
            release.wait(timeout=2.0)
            return value * 2

        worker = LatestPoseInferenceWorker(infer)
        try:
            self.assertTrue(worker.submit({"frame": 1}, 21))
            self.assertFalse(worker.submit({"frame": 2}, 99))
            self.assertIsNone(worker.poll())

            release.set()
            result = None
            for _ in range(100):
                result = worker.poll()
                if result is not None:
                    break
                threading.Event().wait(0.01)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.context, {"frame": 1})
            self.assertEqual(result.output, 42)
            self.assertEqual(worker.skipped_busy_count, 1)
            self.assertEqual(worker.submit_count, 1)
            self.assertEqual(worker.result_count, 1)
            self.assertTrue(worker.submit({"frame": 3}, 7))
            self.assertEqual(worker.submit_count, 2)
        finally:
            release.set()
            worker.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
