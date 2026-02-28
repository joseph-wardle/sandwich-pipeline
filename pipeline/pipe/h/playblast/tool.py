from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import hou
from env_sg import DB_Config

from pipe.db import DB
from pipe.glui.dialogs import MessageDialog
from pipe.h import local
from pipe.util import Playblaster

from .playblaster import HPlayblaster
from .ui import HPlayblastDialog

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from Qt import QtWidgets


def launch_playblast() -> None:
    if local.is_headless():
        MessageDialog(None, "Playblast requires the Houdini UI.", "Playblast").exec_()
        return

    parent = local.get_main_qt_window()
    try:
        conn = DB.Get(DB_Config)
    except Exception as exc:
        log.error("ShotGrid connection failed: %s", exc, exc_info=True)
        MessageDialog(parent, "Could not connect to ShotGrid.", "Playblast").exec_()
        return
    default_shot_code = _resolve_shot_code()
    if not default_shot_code:
        MessageDialog(
            parent,
            "Could not determine the current shot from the scene.",
            "Playblast",
        ).exec_()
        return

    dialog = HPlayblastDialog(parent, conn, default_shot_code)
    if not dialog.exec_():
        return

    shot_code = dialog.shot_code
    if not shot_code:
        MessageDialog(parent, "Please enter a shot code.", "Playblast").exec_()
        return

    try:
        shot = conn.get_shot_by_code(shot_code)
    except Exception as exc:
        log.error("Shot lookup failed for %s: %s", shot_code, exc, exc_info=True)
        MessageDialog(
            parent, f"Shot '{shot_code}' not found in ShotGrid.", "Playblast"
        ).exec_()
        return

    output_base, custom_base = dialog.resolve_output_base_paths()
    if output_base is None:
        MessageDialog(parent, "Unable to build export path.", "Playblast").exec_()
        return

    out_paths: dict[Playblaster.PRESET, list[Path | str]] = {
        Playblaster.PRESET.EDIT_SQ: [output_base]
    }
    if custom_base is not None:
        out_paths[Playblaster.PRESET.EDIT_SQ].append(custom_base)
    playblaster = HPlayblaster().configure(shot, out_paths)

    try:
        playblaster.playblast()
    except Exception as exc:
        log.error("Playblast failed: %s", exc, exc_info=True)
        MessageDialog(
            parent, "Playblast failed. Check the console for details.", "Playblast"
        ).exec_()
        return

    final_path = Path(str(output_base) + f".{Playblaster.PRESET.EDIT_SQ.ext}")
    if dialog.upload_to_shotgrid:
        _upload_stub(parent, final_path)

    message = f"Playblast saved to:\n{final_path}"
    if custom_base is not None:
        custom_final = Path(str(custom_base) + f".{Playblaster.PRESET.EDIT_SQ.ext}")
        message = f"{message}\n\nAdditional export:\n{custom_final}"
    MessageDialog(parent, message, "Playblast").exec_()


def _resolve_shot_code() -> str | None:
    try:
        shot_path = hou.contextOption("SHOT")
    except Exception:
        shot_path = None

    if isinstance(shot_path, (str, Path)) and str(shot_path):
        try:
            return Path(shot_path).name
        except Exception:
            pass

    try:
        hip_path = Path(hou.hipFile.path())
    except Exception:
        return None

    pattern = re.compile(r"[A-Za-z]+_\d+")
    for part in hip_path.parts:
        if pattern.fullmatch(part):
            return part

    return None


def _upload_stub(parent: QtWidgets.QWidget | None, movie_path: Path) -> None:
    log.info("ShotGrid upload requested for %s (not implemented yet).", movie_path)
    MessageDialog(
        parent, "ShotGrid upload is not implemented yet.", "Playblast"
    ).exec_()
