from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.export_coarse_distill_dataset import export_coarse_dataset


class CoarseDistillPipelineTests(unittest.TestCase):
    @staticmethod
    def _points(x_offset: float) -> list[list[float]]:
        return [[x_offset + index, 20.0 + index, 0.9] for index in range(17)]

    def test_export_writes_one_group_safe_row_and_sequence_per_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frames = root / "activity.jsonl"
            poses = root / "poses.jsonl"
            csv_out = root / "features.csv"
            npz_out = root / "sequences.npz"
            meta_out = root / "meta.json"
            activity_rows: list[dict[str, object]] = []
            pose_rows: list[dict[str, object]] = []
            for sample_id, label, offset in (("video-a", "standing", 0.0), ("video-b", "walking", 10.0)):
                for frame_index in range(4):
                    points = self._points(offset + frame_index)
                    activity_rows.append({
                        "sample_id": sample_id, "frame_index": frame_index,
                        "activity_label": label, "risk_label": "normal", "keypoints": points,
                    })
                    pose_rows.append({
                        "sample_id": f"{sample_id}:{frame_index}", "frame_index": frame_index,
                        "bbox_xyxy": [0, 0, 100, 200], "width": 320, "height": 240,
                        "timestamp_ms": frame_index * 33, "keypoints": points,
                    })
            frames.write_text("".join(json.dumps(row) + "\n" for row in activity_rows), encoding="utf-8")
            poses.write_text("".join(json.dumps(row) + "\n" for row in pose_rows), encoding="utf-8")

            report = export_coarse_dataset(frames, poses, csv_out, npz_out, meta_out, sequence_length=4)

            with csv_out.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["sample_id"] for row in rows], ["video-a", "video-b"])
            with np.load(npz_out, allow_pickle=False) as payload:
                self.assertEqual(payload["sequences"].shape, (2, 3, 4, 17, 1))
                self.assertEqual(payload["labels"].tolist(), [0, 1])
                self.assertEqual(payload["sample_ids"].tolist(), ["video-a", "video-b"])
            self.assertEqual(report.label_names, ("standing", "walking"))
            meta = json.loads(meta_out.read_text(encoding="utf-8"))
            self.assertFalse(meta["canonical_21"])
            self.assertTrue(meta["teacher_distillation"])


if __name__ == "__main__":
    unittest.main()
