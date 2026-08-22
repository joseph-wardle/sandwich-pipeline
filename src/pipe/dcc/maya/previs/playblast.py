"""Render previs shots for the viewer: one HUD-burned clip per shot, each routed
to the sequence's playblasts folder."""

from __future__ import annotations

import logging
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
from pipe.core.playblast.confirm import (
    ChosenDisk,
    ChosenShotGrid,
    confirm_clip,
    failure_summary,
)
from pipe.core.previs import codes, playblasts_dir

from pipe.dcc.maya.playblast.previs.take import MTakeConfig, MTakePlayblaster

from .state import PrevisShot

log = logging.getLogger(__name__)

# Both strings are persisted: the settings key names the viewer's remembered
# toggles, and the destination name is what gets remembered under it.
PLAYBLAST_SETTINGS_KEY = "maya_previs_shot"
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


def build_shot_playblasts(
    shots: list[PrevisShot],
    sequence_code: str,
    *,
    previs_root: Path,
) -> ShotPlayblastBatch:
    """Render every shot in `shots`, reporting per-shot failures instead of
    aborting the batch."""
    playblaster = MTakePlayblaster()
    destination = _playblasts_folder(sequence_code, previs_root=previs_root)
    clips: list[PreviewClip] = []
    failed: list[tuple[str, str]] = []
    for shot in shots:
        label = shot.code or shot.id
        try:
            clips.append(_render_shot_playblast(shot, playblaster, destination))
        except PrevisPlayblastError as exc:
            failed.append((label, str(exc)))
        except Exception as exc:  # a render crash on one shot must not abort the rest
            log.exception("Previs playblast failed for shot %s.", label)
            failed.append((label, str(exc) or exc.__class__.__name__))
    return ShotPlayblastBatch(clips=clips, failed=failed)


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
    code = _require_code(previs_shot)
    if not previs_shot.primary:
        raise PrevisPlayblastError(f"Shot {code} has no primary camera to render from.")

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


def _require_code(previs_shot: PrevisShot) -> str:
    """The shot's canonical sticky code, or a PrevisPlayblastError an artist can fix."""
    if not previs_shot.code.strip():
        raise PrevisPlayblastError(
            "This shot has no sequence code yet. Give it a code (e.g. A_010) before "
            "playblasting it."
        )
    try:
        return codes.normalize_code(previs_shot.code)
    except ValueError as exc:
        raise PrevisPlayblastError(str(exc)) from exc


__all__ = [
    "PLAYBLASTS_DESTINATION_NAME",
    "PLAYBLAST_SETTINGS_KEY",
    "PrevisPlayblastError",
    "ShotPlayblastBatch",
    "build_shot_playblasts",
    "deliver_break_out_version",
]
