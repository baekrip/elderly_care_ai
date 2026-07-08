from __future__ import annotations

import unittest

import numpy as np

from edge.clip_blur import blur_frame_head_roi


class ClipBlurTests(unittest.TestCase):
    def test_blurs_head_region_without_changing_frame_shape(self) -> None:
        frame = np.zeros((120, 120, 3), dtype=np.uint8)
        frame[10:50, 10:50] = np.indices((40, 40)).sum(axis=0)[:, :, None] % 255
        keypoints = [[20.0, 20.0, 0.9] for _ in range(17)]
        blurred = blur_frame_head_roi(frame, keypoints, bbox=[10, 10, 80, 110])

        self.assertEqual(blurred.shape, frame.shape)
        self.assertFalse(np.array_equal(blurred[10:50, 10:50], frame[10:50, 10:50]))


if __name__ == "__main__":
    unittest.main()
