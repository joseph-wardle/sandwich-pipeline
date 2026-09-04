"""Render previs shots for the viewer: one clip per shot, each routed to the
sequence's playblasts folder."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import attrs

from pipe.core.playblast import (
    EDIT_FOLDER_ID,
    EDIT_FOLDER_NAME,
    PREVIS_FOLDER_ID,
    Destination,
    DiskDestination,
    FFmpegPreset,
    PreviewClip,
    ScratchEntity,
    ShotEntity,
    ShotGridDestination,
    custom_folder_destination,
)
from pipe.core.playblast.confirm import (
    ChosenDisk,
    ChosenShotGrid,
    confirm_clip,
    failure_summary,
)
from pipe.core.playblast.cut import CutStagingError, stage_cut
from pipe.core.previs import codes, playblasts_dir
from pipe.core.util.paths import get_edit_path

from pipe.dcc.maya.playblast.previs.take import MTakeConfig, MTakePlayblaster
from pipe.dcc.maya.playblast.viewport import ViewportQuality, query_viewport_quality

from . import cameras
from .state import FRAME_START, PrevisShot

log = logging.getLogger(__name__)

# Persisted: the viewer files an artist's remembered destination toggles under it.
PLAYBLAST_SETTINGS_KEY = "maya_previs_shot"
# The cut's toggles are remembered apart from the per-shot ones: it is delivered
# far less often, and its ShotGrid row starts off.
CUT_SETTINGS_KEY = "maya_previs_cut"
SEQUENCE_FOLDER_NAME = "Sequence Folder"
EDIT_UNAVAILABLE = "WIP — coming soon"


class PrevisPlayblastError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


@dataclass
class ShotPlayblastBatch:
    """Rendered clips plus the shots that produced none.

    `failed` is `(shot label, artist-facing reason)` per skipped shot.
    """

    clips: list[PreviewClip]
    failed: list[tuple[str, str]]
    cancelled: bool = False


def render_blocker(shot: PrevisShot) -> str | None:
    """Why `shot` cannot be playblasted, or None if it can."""
    if not shot.code.strip():
        return "no shot code yet"
    if not shot.primary:
        return "no primary camera"
    # The shape, not the namespace: capture resolves a camera shape under it, so
    # a namespace left behind by a deleted camera would pass a liveness check and
    # still fail at render.
    if cameras.camera_shape_for_namespace(shot.primary) is None:
        return f"camera {shot.primary} is missing from the scene"
    return None


def build_shot_playblasts(
    shots: list[PrevisShot],
    sequence_code: str,
    *,
    previs_root: Path,
    quality: ViewportQuality,
    on_shot: Callable[[int, str], bool] | None = None,
) -> ShotPlayblastBatch:
    """Render every shot in `shots`, reporting per-shot failures instead of
    aborting the batch."""
    playblaster = MTakePlayblaster()
    destination = _sequence_folder(sequence_code, previs_root=previs_root)
    clips: list[PreviewClip] = []
    failed: list[tuple[str, str]] = []
    for index, shot in enumerate(shots):
        label = shot.code or shot.id
        if on_shot is not None and not on_shot(index, label):
            return ShotPlayblastBatch(clips=clips, failed=failed, cancelled=True)
        try:
            clips.append(
                _render_shot_playblast(shot, playblaster, destination, quality)
            )
        except PrevisPlayblastError as exc:
            failed.append((label, str(exc)))
        except Exception as exc:  # a render crash on one shot must not abort the rest
            log.exception("Previs playblast failed for shot %s.", label)
            failed.append((label, str(exc) or exc.__class__.__name__))
    return ShotPlayblastBatch(clips=clips, failed=failed)


def build_cut(
    clips: list[PreviewClip],
    *,
    filename: str | None,
    sequence_code: str,
    previs_root: Path,
) -> PreviewClip:
    """The batch as one clip, named after the previs file that produced it."""
    if filename is None:
        raise PrevisPlayblastError(
            "Save this previs file to deliver the whole cut — the cut movie is "
            "named after the file it came from."
        )
    prefix = Path(filename).stem
    try:
        return stage_cut(
            clips,
            label=prefix,
            output_prefix=prefix,
            settings_key=CUT_SETTINGS_KEY,
            destinations=_destinations(
                _sequence_folder(sequence_code, previs_root=previs_root),
                # The sequence proxy is the one Shot a whole-file cut belongs
                # on; the shots in it have their own Versions.
                ShotGridDestination(entity=ShotEntity(sequence_code), default_on=False),
            ),
            frame_start=FRAME_START,
        )
    except CutStagingError as exc:
        raise PrevisPlayblastError(str(exc)) from exc


def deliver_break_out_version(
    shot: PrevisShot, sequence_code: str, *, previs_root: Path
) -> str:
    """Render `shot` and publish it as a ShotGrid Version on its own Shot."""
    folder = _sequence_folder(sequence_code, previs_root=previs_root)
    clip = _render_shot_playblast(
        shot, MTakePlayblaster(), folder, query_viewport_quality(), linked=True
    )
    review = next(d for d in clip.destinations if isinstance(d, ShotGridDestination))
    result = confirm_clip(
        clip,
        (
            ChosenDisk(destination=folder, directory=folder.directory),
            ChosenShotGrid(destination=review),
        ),
        (folder.directory,),
    )
    summary = failure_summary(result)
    if summary:
        return f"{summary} {' '.join(o.detail for o in result.outcomes if not o.ok)}"
    return f"Published {result.basename} to ShotGrid."


def _sequence_folder(sequence_code: str, *, previs_root: Path) -> DiskDestination:
    """Where previs playblasts live: `production/previs/<seq>/playblasts/`."""
    return DiskDestination(
        id=PREVIS_FOLDER_ID,
        name=SEQUENCE_FOLDER_NAME,
        directory=playblasts_dir(sequence_code, previs_root=previs_root),
        preset=FFmpegPreset.EDIT_SQ,
    )


def _edit_folder() -> DiskDestination:
    # The directory is the edit root rather than a guessed department folder
    return DiskDestination(
        id=EDIT_FOLDER_ID,
        name=EDIT_FOLDER_NAME,
        directory=get_edit_path(),
        preset=FFmpegPreset.EDIT_SQ,
        default_on=False,
        unavailable=EDIT_UNAVAILABLE,
    )


def _render_shot_playblast(
    previs_shot: PrevisShot,
    playblaster: MTakePlayblaster,
    folder: DiskDestination,
    quality: ViewportQuality,
    *,
    linked: bool = False,
) -> PreviewClip:
    """Render `previs_shot`'s primary take over its authored source range."""
    blocker = render_blocker(previs_shot)
    if blocker is not None:
        raise PrevisPlayblastError(
            f"{previs_shot.code or 'This shot'} was not rendered: {blocker}."
        )
    code = codes.normalize_code(previs_shot.code)

    config = MTakeConfig(
        camera=previs_shot.primary,
        code=code,
        source_in=previs_shot.source_in,
        source_out=previs_shot.source_out,
        quality=quality,
    )
    clips = playblaster.configure(config).playblast()
    if not clips:
        raise PrevisPlayblastError(f"Shot {code} rendered no frames.")

    return attrs.evolve(
        clips[0],
        label=code,
        output_prefix=code,
        settings_key=PLAYBLAST_SETTINGS_KEY,
        destinations=_destinations(
            folder,
            ShotGridDestination(
                entity=ShotEntity(code) if linked else ScratchEntity(code),
                default_on=linked,
            ),
        ),
    )


def _destinations(
    folder: DiskDestination, review: ShotGridDestination
) -> tuple[Destination, ...]:
    """Where a previs clip can go. One shot or the whole cut, the offer is the
    same. Only the Shot the Version hangs off changes."""
    return (folder, _edit_folder(), custom_folder_destination(), review)


__all__ = [
    "CUT_SETTINGS_KEY",
    "EDIT_UNAVAILABLE",
    "PLAYBLAST_SETTINGS_KEY",
    "SEQUENCE_FOLDER_NAME",
    "PrevisPlayblastError",
    "ShotPlayblastBatch",
    "build_cut",
    "build_shot_playblasts",
    "deliver_break_out_version",
    "render_blocker",
]
