"""Which newer deliveries could stand in for a clip in the cut."""

from __future__ import annotations

import enum

import attrs

from pipe.core.edit.delivery import Delivery, shot_deliveries, shot_frames

# The order work moves through the show. fx and lighting share a stage: a newer
# fx movie doesn't replace a lit shot, nor the other way round.
_STAGES = {"previs": 0, "anim": 1, "cfx": 2, "fx": 3, "lighting": 3, "comp": 4}

# Deliveries start at the shot's first frame or a few handle frames before it.
# Starts further apart mean one movie is numbered in scene time, not shot time.
_MAX_START_GAP = 24


class Kind(enum.Enum):
    UPDATE = "newer from the same department"
    LATER = "later department"
    OTHER = "other department"


@attrs.frozen
class Offer:
    delivery: Delivery
    kind: Kind
    take: range | None
    """Frames of the delivery's movie that cover the clip, counted from 0."""
    problem: str = ""


def offers(current: Delivery, used: range) -> list[Offer]:
    """The newest delivery per department since `current`, in stage order.

    `used` is the frames of `current`'s movie the clip shows, counted from 0.
    """
    deliveries, _ = shot_deliveries(current.shot_code)
    newest = {d.department: d for d in deliveries if d.version > current.version}
    ordered = sorted(newest.values(), key=lambda d: _STAGES.get(d.department, 99))
    return [_offer(current, used, d) for d in ordered]


def _offer(current: Delivery, used: range, delivery: Delivery) -> Offer:
    kind = _kind(current.department, delivery.department)
    current_frames = shot_frames(current.path)
    frames = shot_frames(delivery.path)
    if current_frames is None or frames is None:
        missing = current if current_frames is None else delivery
        return Offer(delivery, kind, None, f"{missing.path.name} has no timecode")
    if abs(frames.start - current_frames.start) > _MAX_START_GAP:
        return Offer(
            delivery,
            kind,
            None,
            f"timecodes don't line up ({current_frames.start} and {frames.start})",
        )
    shown = range(current_frames.start + used.start, current_frames.start + used.stop)
    if shown.start < frames.start or shown.stop > frames.stop:
        return Offer(
            delivery,
            kind,
            None,
            f"covers {frames.start}-{frames.stop - 1}, "
            f"the cut shows {shown.start}-{shown.stop - 1}",
        )
    take = range(shown.start - frames.start, shown.stop - frames.start)
    return Offer(delivery, kind, take)


def _kind(current: str, newer: str) -> Kind:
    if newer == current:
        return Kind.UPDATE
    if newer in _STAGES and current in _STAGES and _STAGES[newer] > _STAGES[current]:
        return Kind.LATER
    return Kind.OTHER
