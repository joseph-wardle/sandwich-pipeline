"""Render previs take previews for the viewer: one HUD-burned clip per shot,
each carrying the manifest-stamp context the viewer needs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import attrs
import maya.cmds as mc

from pipe.core.playblast import PreviewClip, PrevisStamp
from pipe.core.previs import codes

from pipe.dcc.maya.playblast.previs.take import MTakeConfig, MTakePlayblaster

from .playback import FRAME_START, compute_shot_ranges
from .state import PrevisShot, PrevisState

log = logging.getLogger(__name__)

# Viewer key for a take clip's remembered destination toggles.
TAKE_SETTINGS_KEY = "maya_previs_take"


class PrevisExportError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


@dataclass
class TakePreviewBatch:
    """Rendered take previews plus the shots that produced none.

    `fps`/`resolution` come from the playblaster and go into the preview spec.
    `failed` is `(shot label, artist-facing reason)` per skipped shot.
    """

    clips: list[PreviewClip]
    failed: list[tuple[str, str]]
    fps: int
    resolution: tuple[int, int]


def build_take_previews(
    state: PrevisState,
    shots: list[PrevisShot],
    sequence_code: str,
    *,
    previs_root: Path,
) -> TakePreviewBatch:
    """Render a take preview for every shot in `shots`, reporting per-shot
    failures instead of aborting the batch."""
    ranges = compute_shot_ranges(state)
    playblaster = MTakePlayblaster()
    clips: list[PreviewClip] = []
    failed: list[tuple[str, str]] = []
    for shot in shots:
        cut_in, cut_out = ranges.get(shot.id, (FRAME_START, FRAME_START))
        label = shot.code or shot.id
        try:
            clips.append(
                _render_take_preview(
                    shot, cut_in, cut_out, sequence_code, previs_root, playblaster
                )
            )
        except PrevisExportError as exc:
            failed.append((label, str(exc)))
        except Exception as exc:  # a render crash on one shot must not abort the rest
            log.exception("Take preview render failed for shot %s.", label)
            failed.append((label, str(exc) or exc.__class__.__name__))
    return TakePreviewBatch(
        clips=clips,
        failed=failed,
        fps=playblaster.fps,
        resolution=playblaster.resolution,
    )


def _render_take_preview(
    previs_shot: PrevisShot,
    cut_in: int,
    cut_out: int,
    sequence_code: str,
    previs_root: Path,
    playblaster: MTakePlayblaster,
) -> PreviewClip:
    """Render `previs_shot`'s primary over `[cut_in, cut_out]` into a preview clip
    stamped with the manifest context the viewer needs to send it to edit."""
    code = _require_code(previs_shot)
    if not previs_shot.primary:
        raise PrevisExportError(
            f"Shot {code} has no primary camera to render a take from."
        )

    config = MTakeConfig(
        camera=previs_shot.primary, code=code, cut_in=cut_in, cut_out=cut_out
    )
    clips = playblaster.configure(config).playblast()
    if not clips:
        raise PrevisExportError(f"Shot {code} rendered no frames.")

    stamp = PrevisStamp(
        sequence_code=sequence_code,
        shot_code=code,
        camera=previs_shot.primary,
        source_filename=_current_scene_filename(),
        duration_frames=max(0, cut_out - cut_in + 1),
        previs_root=previs_root,
    )
    return attrs.evolve(
        clips[0],
        label=code,
        output_prefix=code,
        settings_key=TAKE_SETTINGS_KEY,
        previs_stamp=stamp,
    )


def _require_code(previs_shot: PrevisShot) -> str:
    """The shot's canonical sticky code, or a PrevisExportError an artist can fix."""
    if not previs_shot.code.strip():
        raise PrevisExportError(
            "This shot has no sequence code yet. Give it a code (e.g. A_010) before "
            "exporting a take."
        )
    try:
        return codes.normalize_code(previs_shot.code)
    except ValueError as exc:
        raise PrevisExportError(str(exc)) from exc


def _current_scene_filename() -> str:
    """Basename of the open Maya scene, or "" if it is unsaved.

    Recorded as the take's `source_filename` for provenance; an unsaved scene is a
    degenerate case that still renders, so it is not blocked here.
    """
    scene = mc.file(query=True, sceneName=True)
    return Path(scene).name if isinstance(scene, str) and scene else ""


__all__ = [
    "TAKE_SETTINGS_KEY",
    "PrevisExportError",
    "TakePreviewBatch",
    "build_take_previews",
]
