from __future__ import annotations

from enum import Enum
from typing import Final, Literal, NoReturn


def assert_never(value: NoReturn) -> NoReturn:
    message = f"Unhandled activity label: {value}"
    raise AssertionError(message)


class ActivityLabel(str, Enum):
    STANDING = "standing"
    WALKING = "walking"
    SITTING = "sitting"
    SITTING_DOWN = "sitting_down"
    STANDING_UP = "standing_up"
    LYING_REST = "lying_rest"
    NO_MOVE_SHORT = "no_move_short"
    NO_MOVE_LONG = "no_move_long"
    BENDING_LONG = "bending_long"
    LYING_ON_FLOOR_UNCERTAIN = "lying_on_floor_uncertain"
    OUT_OF_FRAME_ABNORMAL = "out_of_frame_abnormal"
    UNSTABLE_SIT_TO_STAND = "unstable_sit_to_stand"
    NEAR_FALL = "near_fall"
    STUMBLE = "stumble"
    LOSS_OF_BALANCE = "loss_of_balance"
    SUDDEN_DROP = "sudden_drop"
    FLOOR_PRONE_CANDIDATE = "floor_prone_candidate"
    FALL_CANDIDATE = "fall_candidate"
    FALL_CONFIRMED = "fall_confirmed"
    FALL_THEN_NO_MOVE = "fall_then_no_move"
    COLLAPSE_OUT_OF_FRAME = "collapse_out_of_frame"


LabelTier = Literal["normal", "abnormal", "danger_precursor", "fall"]

CURRENT_COARSE_ACTION_LABELS: Final[tuple[str, ...]] = (
    "STANDING",
    "SITTING",
    "LYING",
    "WALKING",
    "TRANSITION",
    "UNKNOWN",
)

STATIC_POSTURE_LABELS: Final[tuple[str, ...]] = (
    "standing",
    "sitting",
    "lying_rest",
    "bending_candidate",
    "no_move_candidate",
    "unknown",
)

NORMAL_ACTIVITY_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.STANDING,
    ActivityLabel.WALKING,
    ActivityLabel.SITTING,
    ActivityLabel.SITTING_DOWN,
    ActivityLabel.STANDING_UP,
    ActivityLabel.LYING_REST,
    ActivityLabel.NO_MOVE_SHORT,
)

ABNORMAL_ACTIVITY_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.NO_MOVE_LONG,
    ActivityLabel.BENDING_LONG,
    ActivityLabel.LYING_ON_FLOOR_UNCERTAIN,
    ActivityLabel.OUT_OF_FRAME_ABNORMAL,
    ActivityLabel.UNSTABLE_SIT_TO_STAND,
)

DANGER_PRECURSOR_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.NEAR_FALL,
    ActivityLabel.STUMBLE,
    ActivityLabel.LOSS_OF_BALANCE,
    ActivityLabel.SUDDEN_DROP,
    ActivityLabel.FLOOR_PRONE_CANDIDATE,
)

FALL_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.FALL_CANDIDATE,
    ActivityLabel.FALL_CONFIRMED,
    ActivityLabel.FALL_THEN_NO_MOVE,
    ActivityLabel.COLLAPSE_OUT_OF_FRAME,
)

TARGET_ACTIVITY_LABELS: Final[tuple[ActivityLabel, ...]] = (
    *NORMAL_ACTIVITY_LABELS,
    *ABNORMAL_ACTIVITY_LABELS,
    *DANGER_PRECURSOR_LABELS,
    *FALL_LABELS,
)

NORMAL_DAILY_ACTION_LABELS: Final[tuple[str, ...]] = tuple(label.value for label in NORMAL_ACTIVITY_LABELS)
ABNORMAL_ACTION_LABELS: Final[tuple[str, ...]] = tuple(label.value for label in ABNORMAL_ACTIVITY_LABELS)
DANGER_ACTION_LABELS: Final[tuple[str, ...]] = tuple(label.value for label in DANGER_PRECURSOR_LABELS)
FALL_EVENT_LABELS: Final[tuple[str, ...]] = tuple(label.value for label in FALL_LABELS)
TARGET_ACTION_LABELS: Final[tuple[str, ...]] = tuple(label.value for label in TARGET_ACTIVITY_LABELS)


def label_tier(label: ActivityLabel) -> LabelTier:
    match label:
        case (
            ActivityLabel.STANDING
            | ActivityLabel.WALKING
            | ActivityLabel.SITTING
            | ActivityLabel.SITTING_DOWN
            | ActivityLabel.STANDING_UP
            | ActivityLabel.LYING_REST
            | ActivityLabel.NO_MOVE_SHORT
        ):
            return "normal"
        case (
            ActivityLabel.NO_MOVE_LONG
            | ActivityLabel.BENDING_LONG
            | ActivityLabel.LYING_ON_FLOOR_UNCERTAIN
            | ActivityLabel.OUT_OF_FRAME_ABNORMAL
            | ActivityLabel.UNSTABLE_SIT_TO_STAND
        ):
            return "abnormal"
        case (
            ActivityLabel.NEAR_FALL
            | ActivityLabel.STUMBLE
            | ActivityLabel.LOSS_OF_BALANCE
            | ActivityLabel.SUDDEN_DROP
            | ActivityLabel.FLOOR_PRONE_CANDIDATE
        ):
            return "danger_precursor"
        case (
            ActivityLabel.FALL_CANDIDATE
            | ActivityLabel.FALL_CONFIRMED
            | ActivityLabel.FALL_THEN_NO_MOVE
            | ActivityLabel.COLLAPSE_OUT_OF_FRAME
        ):
            return "fall"
        case unreachable:
            assert_never(unreachable)
