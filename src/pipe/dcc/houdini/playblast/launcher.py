"""Launch flow for the Houdini playblast: dialog → flipbook render → viewer."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import hou

from pipe.core.playblast import (
    PREVIEW_SPEC_FILENAME,
    PreviewSpec,
    save_preview_spec,
)
from pipe.core.shotgrid import Shot, ShotGrid
from pipe.core.ui import MessageDialog
from pipe.dcc.houdini import runtime
from pipe.dcc.houdini.playblast.dialog import HPlayblastDialog
from pipe.dcc.houdini.playblast.playblaster import HPlayblaster
from pipe.viewer.spawn import spawn_viewer

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from Qt import QtWidgets

SHOT_CODE_FALLBACK_PATTERN = re.compile(r"[A-Za-z]+_\d{3}(?:_[A-Za-z0-9]+)*")


def launch_playblast() -> None:
    if runtime.is_headless():
        MessageDialog(None, "Playblast requires the Houdini UI.", "Playblast").exec_()
        return

    parent = runtime.get_main_qt_window()
    conn = _resolve_connection_or_report(parent)
    if conn is None:
        return

    dialog = HPlayblastDialog(parent, conn, _resolve_shot_code())
    if not dialog.exec_():
        return

    try:
        shot = _resolve_source_shot(conn, dialog)
    except Exception as exc:
        log.exception("Playblast config generation failed")
        MessageDialog(
            parent,
            f"Could not generate playblast settings.\n\n{exc}",
            "Playblast Error",
        ).exec_()
        return

    camera_path = (
        dialog.custom_camera_path if dialog.selected_source_mode == "custom" else None
    )
    playblaster = HPlayblaster().configure(shot, camera_path=camera_path)
    try:
        clips = playblaster.playblast()
    except Exception as exc:
        log.exception("Playblast export failed")
        MessageDialog(parent, f"Playblast failed.\n\n{exc}", "Playblast Error").exec_()
        return

    if not clips:
        MessageDialog(parent, "Nothing was rendered.", "Playblast").exec_()
        return

    routed_clips = [dialog.routed_clip(clip) for clip in clips]
    spec = PreviewSpec(
        fps=playblaster.fps,
        resolution=playblaster.resolution,
        clips=routed_clips,
    )
    spec_path = routed_clips[0].frames_dir / PREVIEW_SPEC_FILENAME
    try:
        save_preview_spec(spec, spec_path)
        spawn_viewer(spec_path)
    except Exception as exc:
        log.exception("Could not open the playblast viewer")
        MessageDialog(
            parent,
            "The playblast rendered, but the viewer could not open, so "
            f"nothing was saved or uploaded.\n\nReason: {exc}",
            "Playblast Error",
        ).exec_()


def _resolve_connection_or_report(parent: QtWidgets.QWidget | None) -> ShotGrid | None:
    # env_sg holds gitignored production credentials; import lazily so
    # importing this module never requires them.
    from env_sg import DB_Config

    try:
        return ShotGrid.connect(DB_Config)
    except Exception as exc:
        log.error("ShotGrid connection failed: %s", exc, exc_info=True)
        MessageDialog(parent, "Could not connect to ShotGrid.", "Playblast").exec_()
        return None


def _resolve_source_shot(conn: ShotGrid, dialog: HPlayblastDialog) -> Shot:
    if dialog.selected_source_mode == "custom":
        cut_in, cut_out = dialog.custom_frame_range
        if cut_out < cut_in:
            cut_out = cut_in
        return Shot(
            code=dialog.custom_shot_code,
            id=0,
            assets=[],
            cut_in=cut_in,
            cut_out=cut_out,
            cut_duration=max(0, cut_out - cut_in),
            sequence=None,
            set=None,
            sets=[],
        )

    shot_code = dialog.shot_code
    if not shot_code:
        raise ValueError("No shot code was found for Shot Playblast.")
    try:
        return conn.get_shot(code=shot_code)
    except Exception as exc:
        log.error("Shot lookup failed for %s: %s", shot_code, exc, exc_info=True)
        raise ValueError(f"Shot '{shot_code}' not found in ShotGrid.") from exc


def _resolve_shot_code() -> str | None:
    try:
        shot_path = hou.contextOption("SHOT")
    except Exception:
        shot_path = None

    shot_code_from_context = _shot_code_from_context_option(shot_path)
    if shot_code_from_context:
        return shot_code_from_context

    try:
        hip_path = Path(hou.hipFile.path())
    except Exception:
        return None

    shot_code_from_path = _shot_code_from_hip_path(hip_path)
    if shot_code_from_path:
        return shot_code_from_path

    return None


def _shot_code_from_context_option(shot_path: Any) -> str | None:
    if not isinstance(shot_path, (str, Path)):
        return None

    context_token = str(shot_path).strip()
    if not context_token:
        return None

    try:
        candidate = Path(context_token).name.strip()
    except Exception:
        return None

    if candidate and SHOT_CODE_FALLBACK_PATTERN.fullmatch(candidate):
        return candidate
    return None


def _shot_code_from_hip_path(hip_path: Path) -> str | None:
    path_parts = list(hip_path.parts)
    for index, part in enumerate(path_parts[:-1]):
        if part.lower() != "shot":
            continue
        candidate = str(path_parts[index + 1]).strip()
        if SHOT_CODE_FALLBACK_PATTERN.fullmatch(candidate):
            return candidate

    for part in path_parts:
        candidate = str(part).strip()
        if SHOT_CODE_FALLBACK_PATTERN.fullmatch(candidate):
            return candidate

    return None
