"""Join a batch of rendered clips into one clip covering the whole batch."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pipe.core.playblast.clip import Destination, PreviewClip, padded_frame_number
from pipe.core.playblast.tempdir import resolve_playblast_tempdir

log = logging.getLogger(__name__)


class CutStagingError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


def stage_cut(
    clips: Sequence[PreviewClip],
    *,
    label: str,
    output_prefix: str,
    settings_key: str,
    destinations: tuple[Destination, ...],
    frame_start: int,
) -> PreviewClip:
    """One clip spanning `clips` end to end, in the order given."""
    if not clips:
        raise CutStagingError("There are no clips to join into a cut.")

    directory = Path(
        tempfile.mkdtemp(prefix="cut_", dir=str(resolve_playblast_tempdir()))
    )
    basename = output_prefix or "cut"
    frame = frame_start
    copied = 0
    for clip in clips:
        for source_frame in range(clip.frame_start, clip.frame_end + 1):
            source = clip.frame_path(source_frame)
            if not source.exists():
                # Holding the number back keeps the range contiguous; a gap in it
                # is what the encoder reads as the end of the sequence.
                log.warning("Cut skips missing frame %s", source)
                continue
            copied += _stage_frame(
                source, directory / f"{basename}.{padded_frame_number(frame)}.png"
            )
            frame += 1
    if copied:
        # Minutes of disk churn instead of an instant link — worth being able to
        # explain afterwards, but not worth stopping the delivery over.
        log.warning(
            "Cut copied %d frames; the staging filesystem refused links", copied
        )

    if frame == frame_start:
        raise CutStagingError(
            "None of the rendered frames are still on disk, so there is nothing "
            "to join. Playblast again."
        )

    return PreviewClip(
        label=label,
        frames_dir=directory,
        frames_basename=basename,
        frame_start=frame_start,
        frame_end=frame - 1,
        fps=clips[0].fps,
        output_prefix=output_prefix,
        settings_key=settings_key,
        destinations=destinations,
    )


def _stage_frame(source: Path, target: Path) -> bool:
    """Link `source` into place; True if it had to be copied instead."""
    try:
        os.link(source, target)
    except OSError:
        # Same tempdir in practice, so a link is the normal case; a filesystem
        # that refuses one still gets a cut, just at the cost of a copy.
        shutil.copyfile(source, target)
        return True
    return False


__all__ = ["CutStagingError", "stage_cut"]
