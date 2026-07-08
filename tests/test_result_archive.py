from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from server.result_archive import ResultArchive


class ResultArchiveTests(unittest.TestCase):
    def test_archive_writes_request_and_result_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = ResultArchive(
                {
                    "local_archive": {
                        "enabled": True,
                        "save_dir": tmpdir,
                        "candidate_requests_file": "candidate_requests.jsonl",
                        "stgcn_results_file": "stgcn_results.jsonl",
                    }
                }
            )

            archive.write_candidate_request({"camera_id": "cam01", "count": 1})
            archive.write_stgcn_result({"event_id": "evt01", "label": "FALL"})

            request_lines = (Path(tmpdir) / "candidate_requests.jsonl").read_text(encoding="utf-8").splitlines()
            result_lines = (Path(tmpdir) / "stgcn_results.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual(json.loads(request_lines[0])["camera_id"], "cam01")
            self.assertEqual(json.loads(result_lines[0])["label"], "FALL")

    def test_archive_writes_clip_json_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = ResultArchive(
                {
                    "local_archive": {
                        "enabled": True,
                        "save_dir": tmpdir,
                        "clip_json_dir": "clip_json",
                        "clip_requests_file": "server_clip_requests.jsonl",
                        "clip_results_file": "server_clip_results.jsonl",
                    }
                }
            )

            archive.write_clip_request({"event_id": "evt01", "camera_id": "cam01"})
            archive.write_clip_result({"event_id": "evt01", "clip_path": "clip_json/clip_evt01.mp4"})

            request_lines = (Path(tmpdir) / "clip_json" / "server_clip_requests.jsonl").read_text(encoding="utf-8").splitlines()
            result_lines = (Path(tmpdir) / "clip_json" / "server_clip_results.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual(json.loads(request_lines[0])["event_id"], "evt01")
            self.assertEqual(json.loads(result_lines[0])["clip_path"], "clip_json/clip_evt01.mp4")

    def test_archive_writes_pattern_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = ResultArchive(
                {
                    "local_archive": {
                        "enabled": True,
                        "save_dir": tmpdir,
                        "pattern_results_file": "pattern_results.jsonl",
                    }
                }
            )

            archive.write_pattern_result({"patient_id": "P001", "anomalies": [{"type": "PROLONGED_INACTIVITY"}]})

            lines = (Path(tmpdir) / "pattern_results.jsonl").read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[0])
            self.assertEqual(row["patient_id"], "P001")
            self.assertEqual(row["anomalies"][0]["type"], "PROLONGED_INACTIVITY")

    def test_archive_daily_rotation_writes_date_partition_and_prunes_old_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            today = date.today().isoformat()
            old_day = (date.today() - timedelta(days=8)).isoformat()
            old_dir = Path(tmpdir) / "daily" / old_day
            old_dir.mkdir(parents=True)
            old_file = old_dir / "candidate_requests.jsonl"
            old_file.write_text('{"old": true}\n', encoding="utf-8")

            archive = ResultArchive(
                {
                    "local_archive": {
                        "enabled": True,
                        "save_dir": tmpdir,
                        "candidate_requests_file": "candidate_requests.jsonl",
                        "rotation": {
                            "enabled": True,
                            "retention_days": 7,
                        },
                    }
                }
            )

            archive.write_candidate_request({"camera_id": "cam01", "count": 1})

            rotated_file = Path(tmpdir) / "daily" / today / "candidate_requests.jsonl"
            self.assertTrue(rotated_file.exists())
            self.assertEqual(json.loads(rotated_file.read_text(encoding="utf-8").splitlines()[0])["camera_id"], "cam01")
            self.assertFalse(old_file.exists())


if __name__ == "__main__":
    unittest.main()
