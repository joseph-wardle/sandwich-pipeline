from __future__ import annotations


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
