"""Which shot frames a cut shows, read from Resolve's OTIO export.

Resolve exports each clip's source range in its movie's timecode, and a
delivery's timecode is the shot frame, so source ranges are shot frames.
"""

from __future__ import annotations

from pathlib import Path

import attrs
import opentimelineio as otio

from pipe.core.edit.delivery import Delivery, delivery_at, legacy_shot_code


@attrs.frozen
class CutClip:
    delivery: Delivery
    frames: range
    """Shot frames the clip shows."""


@attrs.frozen
class LegacyClip:
    """A shot movie from before deliveries. Its timecode isn't a shot frame."""

    shot_code: str
    path: Path


@attrs.frozen
class ShotSpan:
    frames: range
    """First to last frame the cut shows. A shot used more than once spans
    every use."""
    unused: int
    """Frames inside `frames` that no clip shows. A razor cut leaves none;
    using two separate moments of a shot leaves the frames between."""


@attrs.frozen
class Cut:
    clips: list[CutClip]
    legacy: list[LegacyClip]


def read_cut(path: Path) -> Cut:
    """The enabled clips on enabled video tracks, in timeline order. Movies
    that are neither deliveries nor legacy shot movies are left out."""
    timeline = otio.adapters.read_from_file(str(path))
    placed: list[tuple[int, CutClip | LegacyClip]] = []
    for track in timeline.video_tracks():
        if not track.enabled:
            continue
        # Deep search, so a shot inside a compound clip still counts as in
        # the cut.
        for clip in track.find_clips():
            reference = clip.media_reference
            if not clip.enabled or not isinstance(
                reference, otio.schema.ExternalReference
            ):
                continue
            movie = Path(reference.target_url)
            start = clip.range_in_parent().start_time.to_frames()
            delivery = delivery_at(movie)
            if delivery is not None:
                source = clip.source_range
                first = source.start_time.to_frames()
                frames = range(first, first + source.duration.to_frames())
                placed.append((start, CutClip(delivery, frames)))
            elif shot_code := legacy_shot_code(movie):
                placed.append((start, LegacyClip(shot_code, movie)))
    placed.sort(key=lambda pair: pair[0])
    return Cut(
        [c for _, c in placed if isinstance(c, CutClip)],
        [c for _, c in placed if isinstance(c, LegacyClip)],
    )


def shot_spans(clips: list[CutClip]) -> dict[str, ShotSpan]:
    """Each shot's span, in order of first appearance."""
    shown: dict[str, set[int]] = {}
    for clip in clips:
        shown.setdefault(clip.delivery.shot_code, set()).update(clip.frames)
    return {
        code: ShotSpan(range(min(f), max(f) + 1), max(f) + 1 - min(f) - len(f))
        for code, f in shown.items()
    }
