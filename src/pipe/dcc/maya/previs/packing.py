"""Track assignment for the timeline view: which display row each shot draws on."""

from __future__ import annotations

from collections.abc import Sequence

from .state import PrevisShot


def assign_tracks(shots: Sequence[PrevisShot]) -> list[int]:
    """Display track index per shot, in `shots` order."""
    tracks = [0] * len(shots)
    last_occupied: list[int] = []  # last source frame drawn on each track
    for index in sorted(range(len(shots)), key=lambda i: (shots[i].source_in, i)):
        shot = shots[index]
        track = next(
            (t for t, end in enumerate(last_occupied) if end < shot.source_in),
            len(last_occupied),
        )
        if track == len(last_occupied):
            last_occupied.append(shot.source_out)
        else:
            last_occupied[track] = shot.source_out
        tracks[index] = track
    return tracks
