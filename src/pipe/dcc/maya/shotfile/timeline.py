from __future__ import annotations

from typing import NamedTuple

import maya.cmds as mc
from timeline_marker.ui import TimelineMarker  # type: ignore[import-not-found]

from pipe.core.shotgrid import Shot, ShotGridError

_Cut = tuple[int, int]

_ACKNOWLEDGED_CUT = "acknowledgedCut"
_UPDATE = "Update"
_KEEP = "Keep"


class _Segment(NamedTuple):
    label: str
    color: tuple[int, int, int]
    length: int


_PREROLL = (
    _Segment("Rest Pose @Origin", (70, 0, 0), 8),
    _Segment("Rest Pose -> Windup", (150, 0, 0), 8),
    _Segment("Hold Windup", (255, 0, 0), 5),
    _Segment("Windup", (128, 128, 0), 5),
    _Segment("Head", (128, 255, 128), 5),
)
_TAIL = _Segment("Tail", (100, 160, 255), 5)


def sync_shot_timeline(shot: Shot) -> None:
    try:
        cut = shot.frame_range
    except ShotGridError as exc:
        mc.warning(f"Timeline not set: {exc}")
        return

    acknowledged = _acknowledged_cut()
    if acknowledged == cut:
        return
    if acknowledged is None or _confirm_update(shot.code, acknowledged, cut):
        _set_timeline(cut)
    _acknowledge(cut)


def _acknowledged_cut() -> _Cut | None:
    stored = mc.fileInfo(_ACKNOWLEDGED_CUT, query=True)
    if not stored:
        return None
    cut_in, cut_out = stored[0].split()
    return int(cut_in), int(cut_out)


def _acknowledge(cut: _Cut) -> None:
    mc.fileInfo(_ACKNOWLEDGED_CUT, f"{cut[0]} {cut[1]}")


def _confirm_update(code: str | None, old: _Cut, new: _Cut) -> bool:
    answer = mc.confirmDialog(
        title="Timeline",
        message=(
            f"{code}'s cut changed in ShotGrid from {old[0]}–{old[1]} "
            f"to {new[0]}–{new[1]}.\n\nUpdate this scene's timeline to match?"
        ),
        button=[_UPDATE, _KEEP],
        defaultButton=_UPDATE,
        cancelButton=_KEEP,
        dismissString=_KEEP,
    )
    return answer == _UPDATE


def _set_timeline(cut: _Cut) -> None:
    cut_in, cut_out = cut
    shot_body = _Segment("Animate!", (0, 255, 0), cut_out - cut_in + 1)
    frame_segments = [
        segment
        for segment in (*_PREROLL, shot_body, _TAIL)
        for _ in range(segment.length)
    ]
    start = cut_in - sum(segment.length for segment in _PREROLL)
    end = start + len(frame_segments) - 1

    TimelineMarker.set(
        range(start, end + 1),
        [segment.color for segment in frame_segments],
        [segment.label for segment in frame_segments],
    )
    mc.playbackOptions(
        animationStartTime=start, animationEndTime=end, minTime=start, maxTime=end
    )
