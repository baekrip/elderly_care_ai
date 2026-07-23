from __future__ import annotations

from enum import Enum
from typing import Final, Literal, NoReturn


def assert_never(value: NoReturn) -> NoReturn:
    message = f"Unhandled activity label: {value}"
    raise AssertionError(message)


class ActivityLabel(str, Enum):
    STANDING = "standing"
    SITTING = "sitting"
    WALKING = "walking"
    LYING = "lying"
    ROOM_EXIT = "room_exit"
    SLEEPING = "sleeping"
    FALL_DOWN = "fall_down"
    NO_MOVE_LONG = "no_move_long"


LabelTier = Literal["normal", "abnormal", "fall"]

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
    "lying",
    "bending_candidate",
    "no_move_candidate",
    "unknown",
)

NORMAL_ACTIVITY_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.STANDING,
    ActivityLabel.SITTING,
    ActivityLabel.WALKING,
    ActivityLabel.LYING,
    ActivityLabel.SLEEPING,
)

ABNORMAL_ACTIVITY_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.ROOM_EXIT,
    ActivityLabel.NO_MOVE_LONG,
)

DANGER_PRECURSOR_LABELS: Final[tuple[ActivityLabel, ...]] = ()

FALL_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.FALL_DOWN,
)

TARGET_ACTIVITY_LABELS: Final[tuple[ActivityLabel, ...]] = (
    ActivityLabel.STANDING,
    ActivityLabel.SITTING,
    ActivityLabel.WALKING,
    ActivityLabel.LYING,
    ActivityLabel.ROOM_EXIT,
    ActivityLabel.SLEEPING,
    ActivityLabel.FALL_DOWN,
    ActivityLabel.NO_MOVE_LONG,
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
            | ActivityLabel.SITTING
            | ActivityLabel.WALKING
            | ActivityLabel.LYING
            | ActivityLabel.SLEEPING
        ):
            return "normal"
        case (
            ActivityLabel.ROOM_EXIT
            | ActivityLabel.NO_MOVE_LONG
        ):
            return "abnormal"
        case ActivityLabel.FALL_DOWN:
            return "fall"
        case unreachable:
            assert_never(unreachable)
