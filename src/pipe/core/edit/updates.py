"""Which newer deliveries could stand in for a clip in the cut."""

from __future__ import annotations

import attrs

from pipe.core.edit.delivery import Delivery, shot_deliveries, shot_frames

# The order work moves through the show. A newer movie from the same rank or
# higher starts ticked; the editor makes the final call by flipping takes.
_RANKS = {"previs": 0, "anim": 1, "cfx": 2, "fx": 3, "lighting": 4, "comp": 5}

# Deliveries start at the shot's first frame or a few handle frames before it.
# Starts further apart mean one movie is numbered in scene time, not shot time.
_MAX_START_GAP = 24


@attrs.frozen
class Offer:
    delivery: Delivery
    preferred: bool
    take: range | None
    """Frames of the delivery's movie that cover the clip, counted from 0."""
    problem: str = ""


def offers(current: Delivery, used: range) -> list[Offer]:
    """The newest delivery per department since `current`, lowest rank first.

    `used` is the frames of `current`'s movie the clip shows, counted from 0.
    """
    deliveries, _ = shot_deliveries(current.shot_code)
    newest = {d.department: d for d in deliveries if d.version > current.version}
    ordered = sorted(newest.values(), key=lambda d: rank(d.department))
    return [_offer(current, used, d) for d in ordered]


def _offer(current: Delivery, used: range, delivery: Delivery) -> Offer:
    preferred = rank(delivery.department) >= rank(current.department)
    current_frames = shot_frames(current.path)
    frames = shot_frames(delivery.path)
    if current_frames is None or frames is None:
        missing = current if current_frames is None else delivery
        return Offer(delivery, preferred, None, f"{missing.path.name} has no timecode")
    if abs(frames.start - current_frames.start) > _MAX_START_GAP:
        return Offer(
            delivery,
            preferred,
            None,
            f"timecodes don't line up ({current_frames.start} and {frames.start})",
        )
    shown = range(current_frames.start + used.start, current_frames.start + used.stop)
    if shown.start < frames.start or shown.stop > frames.stop:
        return Offer(
            delivery,
            preferred,
            None,
            f"covers {frames.start}-{frames.stop - 1}, "
            f"the cut shows {shown.start}-{shown.stop - 1}",
        )
    take = range(shown.start - frames.start, shown.stop - frames.start)
    return Offer(delivery, preferred, take)


def rank(department: str) -> int:
    return _RANKS.get(department, -1)
