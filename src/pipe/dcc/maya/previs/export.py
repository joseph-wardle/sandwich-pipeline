"""Export previs takes: render a shot's primary to an immutable take and stamp it.

Problems an artist can fix (no primary, no code) are raised as `PrevisExportError`,
whose message is safe to show in a dialog. `export_all_takes` turns those into
per-shot report lines instead of aborting the batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import maya.cmds as mc

from pipe.core.previs import (
    codes,
    load_manifest,
    mutate_manifest,
    naming,
    playblasts_dir,
)
from pipe.core.previs.model import Take
from pipe.core.playblast import FFmpegPreset

from pipe.dcc.maya.playblast.previs.take import MTakeConfig, MTakePlayblaster

from .playback import FRAME_START, compute_shot_ranges
from .state import PrevisShot, PrevisState, utcnow_iso

log = logging.getLogger(__name__)

# The editorial DNxHD `.mov` is the take's delivery format. Its extension must be
# `naming.TAKE_SUFFIX`, since the take path is built from `take_filename` and the
# playblaster appends this preset's ext to name the file on disk.
_TAKE_PRESET = FFmpegPreset.EDIT_SQ


class PrevisExportError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


@dataclass
class TakeResult:
    """The outcome of one successful take export.

    `previous_duration` is the length of the take this shot delivered before, or
    None if this is its first take — so `length_delta` can distinguish "no change"
    (0) from "nothing to compare against yet" (None).
    """

    code: str
    version: int
    path: Path
    duration_frames: int
    previous_duration: int | None

    @property
    def length_delta(self) -> int | None:
        if self.previous_duration is None:
            return None
        return self.duration_frames - self.previous_duration


@dataclass
class BatchResult:
    exported: list[TakeResult]
    # (shot label, artist-facing reason) for every shot that produced no take.
    failed: list[tuple[str, str]]


def export_take(
    previs_shot: PrevisShot,
    cut_in: int,
    cut_out: int,
    sequence_code: str,
    *,
    previs_root: Path | None = None,
) -> TakeResult:
    """Render `previs_shot`'s primary over `[cut_in, cut_out]` into a new take.

    Renders first, then stamps the manifest: a render failure leaves no phantom
    take record, and the take version comes from the manifest (not the files on
    disk), so an un-stamped orphan `.mov` from a failed prior run is reclaimed
    rather than skipped.
    """
    code = _require_code(previs_shot)
    if not previs_shot.primary:
        raise PrevisExportError(
            f"Shot {code} has no primary camera to render a take from."
        )

    manifest = load_manifest(sequence_code, previs_root=previs_root)
    version = manifest.next_take_version(code)
    prior = manifest.current_take(code)
    previous_duration = prior.duration_frames if prior is not None else None

    take_name = naming.take_filename(code, version)
    take_path = playblasts_dir(sequence_code, previs_root=previs_root) / take_name
    duration_frames = max(0, cut_out - cut_in + 1)

    _render_take(previs_shot.primary, code, cut_in, cut_out, take_path)

    take = Take(
        version=version,
        source_filename=_current_scene_filename(),
        camera=previs_shot.primary,
        created_at=utcnow_iso(),
        duration_frames=duration_frames,
    )
    mutate_manifest(
        sequence_code,
        lambda m: m.add_take(code, take),
        previs_root=previs_root,
    )
    log.info("Exported take %s of %s in sequence %s.", version, code, sequence_code)
    return TakeResult(
        code=code,
        version=version,
        path=take_path,
        duration_frames=duration_frames,
        previous_duration=previous_duration,
    )


def export_all_takes(
    state: PrevisState,
    sequence_code: str,
    *,
    previs_root: Path | None = None,
) -> BatchResult:
    """Export a take for every shot, reporting per-shot failures instead of aborting.

    Frame ranges come from the same layout the dailies sequence uses, so each take
    matches that shot's slice of the sequence.
    """
    ranges = compute_shot_ranges(state)
    exported: list[TakeResult] = []
    failed: list[tuple[str, str]] = []
    for shot in state.shots:
        cut_in, cut_out = ranges.get(shot.id, (FRAME_START, FRAME_START))
        label = shot.code or shot.id
        try:
            exported.append(
                export_take(
                    shot, cut_in, cut_out, sequence_code, previs_root=previs_root
                )
            )
        except PrevisExportError as exc:
            failed.append((label, str(exc)))
        except Exception as exc:  # a render/encode crash on one shot must not abort
            log.exception("Take export failed for shot %s.", label)
            failed.append((label, str(exc) or exc.__class__.__name__))
    return BatchResult(exported=exported, failed=failed)


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


def _render_take(
    camera: str, code: str, cut_in: int, cut_out: int, take_path: Path
) -> None:
    # `with_suffix("")` strips `naming.TAKE_SUFFIX`; the playblaster re-adds the
    # preset's ext (`.mov`) to reach `take_path`.
    config = MTakeConfig(
        camera=camera,
        code=code,
        cut_in=cut_in,
        cut_out=cut_out,
        paths={_TAKE_PRESET: [take_path.with_suffix("")]},
    )
    MTakePlayblaster().configure(config).playblast()


def _current_scene_filename() -> str:
    """Basename of the open Maya scene, or "" if it is unsaved.

    Recorded as the take's `source_filename` for provenance; an unsaved scene is a
    degenerate case that still exports, so it is not blocked here.
    """
    scene = mc.file(query=True, sceneName=True)
    return Path(scene).name if isinstance(scene, str) and scene else ""


__all__ = [
    "PrevisExportError",
    "TakeResult",
    "BatchResult",
    "export_take",
    "export_all_takes",
]
