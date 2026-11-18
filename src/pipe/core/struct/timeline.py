from __future__ import annotations

from typing import TYPE_CHECKING

import attrs

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot

from .json_serializable import JsonSerializable

PREROLL_DURATION = 55
HEAD_START = 996
HANDLE_DURATION = 5


@attrs.define(frozen=True)
class Timeline(JsonSerializable):  # ty: ignore[invalid-frozen-dataclass-subclass]
    start: int
    end: int
    head_duration: int = attrs.field(default=5)
    tail_duration: int = attrs.field(default=5)
    preroll_duration: int = attrs.field(default=PREROLL_DURATION)
    head: int = attrs.field(
        init=False,
        default=attrs.Factory(lambda s: s.start - s.head_duration, takes_self=True),
    )
    tail: int = attrs.field(
        init=False,
        default=attrs.Factory(lambda s: s.end + s.tail_duration, takes_self=True),
    )
    preroll: int = attrs.field(
        init=False,
        default=attrs.Factory(lambda s: s.head - s.preroll_duration, takes_self=True),
    )

    @classmethod
    def from_shot(
        cls: type[Timeline], shot: Shot, preroll_duration: int = PREROLL_DURATION
    ) -> Timeline:
        cut_in, cut_out = shot.frame_range
        return cls(
            start=min(HEAD_START, cut_in),
            end=cut_out + HANDLE_DURATION,
            preroll_duration=preroll_duration,
            head_duration=5,
            tail_duration=5,
        )


def preroll_warning(shot: Shot) -> str | None:
    """Warn when the start frame is early enough to mess up preroll frames for CFX"""
    cut_in, _ = shot.frame_range
    if cut_in >= HEAD_START:
        return None
    timeline = Timeline.from_shot(shot)
    return (
        f"{shot.code} starts on frame {cut_in}, before {HEAD_START}, so its "
        f"preroll moved {HEAD_START - cut_in} frames earlier and now starts on "
        f"{timeline.preroll}. CFX sims that start on a fixed frame need the same "
        "move."
    )


def head_handle(cut_in: int) -> int:
    """Editorial's handle frames before `cut_in`, cut short where the head is."""
    return max(0, min(HANDLE_DURATION, cut_in - HEAD_START))
