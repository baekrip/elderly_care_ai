from __future__ import annotations

import unittest

from shared.labels import (
    ABNORMAL_ACTION_LABELS,
    CURRENT_COARSE_ACTION_LABELS,
    DANGER_ACTION_LABELS,
    FALL_EVENT_LABELS,
    NORMAL_DAILY_ACTION_LABELS,
    TARGET_ACTION_LABELS,
)


class LabelSchemaTests(unittest.TestCase):
    def test_target_action_labels_match_documented_groups(self) -> None:
        self.assertEqual(
            NORMAL_DAILY_ACTION_LABELS,
            (
                "standing",
                "walking",
                "sitting",
                "sitting_down",
                "standing_up",
                "lying_rest",
                "no_move_short",
            ),
        )
        self.assertEqual(
            ABNORMAL_ACTION_LABELS,
            (
                "no_move_long",
                "bending_long",
                "lying_on_floor_uncertain",
                "out_of_frame_abnormal",
                "unstable_sit_to_stand",
            ),
        )
        self.assertEqual(
            DANGER_ACTION_LABELS,
            (
                "near_fall",
                "stumble",
                "loss_of_balance",
                "sudden_drop",
                "floor_prone_candidate",
            ),
        )
        self.assertEqual(
            FALL_EVENT_LABELS,
            (
                "fall_candidate",
                "fall_confirmed",
                "fall_then_no_move",
                "collapse_out_of_frame",
            ),
        )
        self.assertEqual(
            TARGET_ACTION_LABELS,
            (
                *NORMAL_DAILY_ACTION_LABELS,
                *ABNORMAL_ACTION_LABELS,
                *DANGER_ACTION_LABELS,
                *FALL_EVENT_LABELS,
            ),
        )

    def test_current_coarse_model_labels_are_preserved(self) -> None:
        self.assertEqual(
            CURRENT_COARSE_ACTION_LABELS,
            ("STANDING", "SITTING", "LYING", "WALKING", "TRANSITION", "UNKNOWN"),
        )


if __name__ == "__main__":
    unittest.main()
