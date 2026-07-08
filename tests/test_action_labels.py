from __future__ import annotations

import unittest

from shared.labels import (
    ABNORMAL_ACTIVITY_LABELS,
    DANGER_PRECURSOR_LABELS,
    FALL_LABELS,
    NORMAL_ACTIVITY_LABELS,
    TARGET_ACTIVITY_LABELS,
    ActivityLabel,
    label_tier,
)


class ActionLabelTests(unittest.TestCase):
    def test_target_labels_keep_expected_order_and_count(self) -> None:
        self.assertEqual(len(TARGET_ACTIVITY_LABELS), 21)
        self.assertEqual(TARGET_ACTIVITY_LABELS[0], ActivityLabel.STANDING)
        self.assertEqual(TARGET_ACTIVITY_LABELS[-1], ActivityLabel.COLLAPSE_OUT_OF_FRAME)

    def test_label_tier_returns_documented_groups(self) -> None:
        self.assertEqual(label_tier(ActivityLabel.WALKING), "normal")
        self.assertEqual(label_tier(ActivityLabel.NO_MOVE_LONG), "abnormal")
        self.assertEqual(label_tier(ActivityLabel.NEAR_FALL), "danger_precursor")
        self.assertEqual(label_tier(ActivityLabel.FALL_CONFIRMED), "fall")

    def test_group_tuples_do_not_overlap(self) -> None:
        grouped = (
            *NORMAL_ACTIVITY_LABELS,
            *ABNORMAL_ACTIVITY_LABELS,
            *DANGER_PRECURSOR_LABELS,
            *FALL_LABELS,
        )

        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(tuple(grouped), TARGET_ACTIVITY_LABELS)


if __name__ == "__main__":
    unittest.main()
