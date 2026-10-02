from __future__ import annotations

import maya.cmds as mc
from timeline_marker.ui import TimelineMarker  # type: ignore[import-not-found]

from pipe.core.shotgrid import Shot, ShotGridError

# fileInfo key: the ShotGrid cut this scene's timeline was last checked against,
# stored as "<cut_in> <cut_out>".
_CUT_FILE_INFO = "timelineCut"


def timeline_generator(
    # Each segment is (label, (R, G, B) 0-255, duration_in_frames)
    pre_roll: list[tuple[str, tuple[int, int, int], int]],
    roll: list[tuple[str, tuple[int, int, int], int]],
    /,
    # First frame of the "roll" section (usually the shot start).
    start_frame: int = 1001,
) -> tuple[list[int], list[tuple[int, int, int]], list[str]]:
    # Returns: (frames, colors, comments) where frames are frame numbers,
    # colors are per-frame RGB tuples, and comments are per-frame labels.
    colors = []
    comments = []
    pre_duration = 0
    post_duration = 0

    for comment, color, duration in pre_roll:
        comments += [comment] * duration
        colors += [color] * duration
        pre_duration += duration
    for comment, color, duration in roll:
        comments += [comment] * duration
        colors += [color] * duration
        post_duration += duration

    frames = list(range(start_frame - pre_duration, start_frame + post_duration))
    return frames, colors, comments


def shot_timeline_generator(
    # shot_duration is the "Animate!" segment length in frames.
    shot_duration: int,
    shot_start_frame: int,
) -> tuple[list[int], list[tuple[int, int, int]], list[str]]:
    return timeline_generator(
        # Preroll segments shown before start_frame.
        [
            ("Rest Pose @Origin", (70, 0, 0), 8),
            ("Rest Pose -> Windup", (150, 0, 0), 8),
            ("Hold Windup", (255, 0, 0), 5),
            ("Windup", (128, 128, 0), 5),
            ("Head", (128, 255, 128), 5),
        ],
        # Roll segments starting at start_frame.
        [
            ("Animate!", (0, 255, 0), shot_duration),
            ("Tail", (100, 160, 255), 5),
        ],
        start_frame=shot_start_frame,
    )


def sync_shot_timeline(shot: Shot) -> None:
    """Put the scene on `shot`'s timeline, asking first when the cut has changed
    in ShotGrid since the scene was last checked."""
    # Batch Maya has no time slider to mark and nobody to ask.
    if mc.about(batch=True):
        return
    try:
        cut = shot.frame_range
    except ShotGridError as exc:
        mc.warning(f"Timeline not set: {exc}")
        return

    known = _recorded_cut()
    if known == cut:
        return
    # A scene with no recorded cut is new, or was last saved when every open
    # reset the timeline unasked, so there is no change to ask about.
    if known is None or _confirm_update(shot, known):
        _apply_timeline(*cut)
    # Recorded even when declined, so each change is asked about once.
    mc.fileInfo(_CUT_FILE_INFO, f"{cut[0]} {cut[1]}")


def _recorded_cut() -> tuple[int, int] | None:
    value = mc.fileInfo(_CUT_FILE_INFO, query=True)
    if not value:
        return None
    cut_in, cut_out = value[0].split()
    return int(cut_in), int(cut_out)


def _confirm_update(shot: Shot, known: tuple[int, int]) -> bool:
    cut_in, cut_out = shot.frame_range
    answer = mc.confirmDialog(
        title="Timeline",
        message=(
            f"{shot.code}'s cut changed in ShotGrid from {known[0]}–{known[1]} "
            f"to {cut_in}–{cut_out}.\n\nUpdate this scene's timeline to match?"
        ),
        button=["Update", "Keep"],
        defaultButton="Update",
        cancelButton="Keep",
        dismissString="Keep",
    )
    return answer == "Update"


def _apply_timeline(cut_in: int, cut_out: int) -> None:
    # The duration comes from the cut range, never ShotGrid's Cut Duration: that
    # field is typed by hand and goes stale when only Cut Out is edited.
    frames, colors, comments = shot_timeline_generator(cut_out - cut_in + 1, cut_in)
    TimelineMarker.set(frames, colors, comments)
    mc.playbackOptions(
        animationStartTime=frames[0],
        animationEndTime=frames[-1],
        minTime=frames[0],
        maxTime=frames[-1],
    )
