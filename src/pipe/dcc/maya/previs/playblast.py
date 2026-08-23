"""Render previs shots for the viewer: one HUD-burned clip per shot, each routed
to the sequence's playblasts folder."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import attrs

from pipe.core.playblast import (
    EDIT_FOLDER_ID,
    Destination,
    DiskDestination,
    FFmpegPreset,
    PreviewClip,
    ScratchEntity,
    ShotEntity,
    ShotGridDestination,
)
from pipe.core.playblast.cut import CutStagingError, stage_cut
from pipe.core.playblast.confirm import (
    ChosenDisk,
    ChosenShotGrid,
    confirm_clip,
    failure_summary,
)
from pipe.core.previs import codes, playblasts_dir

from pipe.dcc.maya.playblast.previs.take import MTakeConfig, MTakePlayblaster

from . import cameras
from .state import FRAME_START, PrevisShot

log = logging.getLogger(__name__)

# Both strings are persisted: the settings key names the viewer's remembered
# toggles, and the destination name is what gets remembered under it.
PLAYBLAST_SETTINGS_KEY = "maya_previs_shot"
# The cut's toggles are remembered apart from the per-shot ones: it is delivered
# far less often, and its ShotGrid row starts off.
CUT_SETTINGS_KEY = "maya_previs_cut"
PLAYBLASTS_DESTINATION_NAME = "Previs Playblasts"


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
    try:
        codes.normalize_code(shot.code)
    except ValueError as exc:
        return str(exc)
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
    on_shot: Callable[[int, str], bool] | None = None,
) -> ShotPlayblastBatch:
    """Render every shot in `shots`, reporting per-shot failures instead of
    aborting the batch."""
    playblaster = MTakePlayblaster()
    destination = _playblasts_folder(sequence_code, previs_root=previs_root)
    clips: list[PreviewClip] = []
    failed: list[tuple[str, str]] = []
    for index, shot in enumerate(shots):
        label = shot.code or shot.id
        if on_shot is not None and not on_shot(index, label):
            return ShotPlayblastBatch(clips=clips, failed=failed, cancelled=True)
        try:
            clips.append(_render_shot_playblast(shot, playblaster, destination))
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
            destinations=(
                _playblasts_folder(sequence_code, previs_root=previs_root),
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
    folder = _playblasts_folder(sequence_code, previs_root=previs_root)
    clip = _render_shot_playblast(shot, MTakePlayblaster(), folder, linked=True)
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


def _playblasts_folder(sequence_code: str, *, previs_root: Path) -> DiskDestination:
    return DiskDestination(
        id=EDIT_FOLDER_ID,
        name=PLAYBLASTS_DESTINATION_NAME,
        directory=playblasts_dir(sequence_code, previs_root=previs_root),
        preset=FFmpegPreset.EDIT_SQ,
    )


def _render_shot_playblast(
    previs_shot: PrevisShot,
    playblaster: MTakePlayblaster,
    destination: DiskDestination,
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

    # `MTakeConfig` names its range cut_in/cut_out, but a playblast samples scene
    # frames — so it gets the shot's source range.
    config = MTakeConfig(
        camera=previs_shot.primary,
        code=code,
        cut_in=previs_shot.source_in,
        cut_out=previs_shot.source_out,
    )
    clips = playblaster.configure(config).playblast()
    if not clips:
        raise PrevisPlayblastError(f"Shot {code} rendered no frames.")

    return attrs.evolve(
        clips[0],
        label=code,
        output_prefix=code,
        settings_key=PLAYBLAST_SETTINGS_KEY,
        destinations=_destinations_for(code, destination, linked=linked),
    )


def _destinations_for(
    code: str, folder: DiskDestination, *, linked: bool
) -> tuple[Destination, ...]:
    """The shot's delivery folder, plus its ShotGrid Version."""
    entity = ShotEntity(code) if linked else ScratchEntity(code)
    return (folder, ShotGridDestination(entity=entity, default_on=linked))


__all__ = [
    "CUT_SETTINGS_KEY",
    "PLAYBLASTS_DESTINATION_NAME",
    "PLAYBLAST_SETTINGS_KEY",
    "PrevisPlayblastError",
    "ShotPlayblastBatch",
    "build_cut",
    "build_shot_playblasts",
    "deliver_break_out_version",
    "render_blocker",
]
