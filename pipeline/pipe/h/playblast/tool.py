from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import hou
from env_sg import DB_Config

from pipe.db import DB
from pipe.glui.dialogs import MessageDialog
from pipe.h import local
from pipe.playblast_artist import resolve_artist_display_name
from pipe.util import Playblaster

from .playblaster import HPlayblaster
from .ui import HPlayblastDialog

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from Qt import QtWidgets


@dataclass(frozen=True)
class MayaPlayblastParityTarget:
    """Maya behavior that Houdini should mirror during feature unification."""

    name: str
    maya_reference: str
    expected_houdini_behavior: str


MAYA_PARITY_TARGETS: tuple[MayaPlayblastParityTarget, ...] = (
    MayaPlayblastParityTarget(
        name="export_orchestration",
        maya_reference="pipe.m.playblast.ui.PlayblastDialog.do_export",
        expected_houdini_behavior=(
            "Use the same staged flow: generate config, validate, run local export, "
            "run post-export actions, then show a single final summary dialog."
        ),
    ),
    MayaPlayblastParityTarget(
        name="post_export_hook",
        maya_reference="pipe.m.playblast.ui.PlayblastDialog._after_local_playblast",
        expected_houdini_behavior=(
            "Keep local exports successful even when optional post-export actions fail."
        ),
    ),
    MayaPlayblastParityTarget(
        name="shot_mode_upload_wiring",
        maya_reference="pipe.m.playblast.previs.PrevisPlayblastDialog._upload_shot_playblast_to_shotgrid",
        expected_houdini_behavior=(
            "Wire ShotGrid uploads as an optional step after successful local output."
        ),
    ),
    MayaPlayblastParityTarget(
        name="deterministic_upload_source_selection",
        maya_reference="pipe.m.playblast.previs.PrevisPlayblastDialog._resolve_shotgrid_upload_movie_path",
        expected_houdini_behavior=(
            "Resolve upload movie path deterministically with explicit destination preference."
        ),
    ),
)


@dataclass(frozen=True)
class HoudiniPlayblastLaunchContext:
    """Resolved inputs used by the current Houdini local playblast flow."""

    shot_code: str
    output_base: Path
    custom_output_base: Path | None
    upload_to_shotgrid: bool


def launch_playblast() -> None:
    if local.is_headless():
        MessageDialog(None, "Playblast requires the Houdini UI.", "Playblast").exec_()
        return

    parent = local.get_main_qt_window()
    conn = _resolve_connection_or_report(parent)
    if conn is None:
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

    context = _build_launch_context_or_report(dialog, parent)
    if context is None:
        return

    shot = _resolve_shot_or_report(conn, context.shot_code, parent)
    if shot is None:
        return

    out_paths = _build_output_paths(context)
    playblaster = HPlayblaster().configure(shot, out_paths)
    if not _run_local_playblast_or_report(playblaster, parent):
        return

    final_primary_movie = _final_movie_path(
        context.output_base,
        Playblaster.PRESET.EDIT_SQ,
    )
    if context.upload_to_shotgrid:
        _upload_stub(parent, final_primary_movie)

    _show_success_dialog(parent, context, final_primary_movie)


def _resolve_connection_or_report(parent: QtWidgets.QWidget | None) -> Any | None:
    try:
        return DB.Get(DB_Config)
    except Exception as exc:
        log.error("ShotGrid connection failed: %s", exc, exc_info=True)
        MessageDialog(parent, "Could not connect to ShotGrid.", "Playblast").exec_()
        return None


def _build_launch_context_or_report(
    dialog: HPlayblastDialog,
    parent: QtWidgets.QWidget | None,
) -> HoudiniPlayblastLaunchContext | None:
    shot_code = dialog.shot_code
    if not shot_code:
        MessageDialog(parent, "Please enter a shot code.", "Playblast").exec_()
        return None

    output_base, custom_output_base = dialog.resolve_output_base_paths()
    if output_base is None:
        MessageDialog(parent, "Unable to build export path.", "Playblast").exec_()
        return None

    return HoudiniPlayblastLaunchContext(
        shot_code=shot_code,
        output_base=output_base,
        custom_output_base=custom_output_base,
        upload_to_shotgrid=dialog.upload_to_shotgrid,
    )


def _resolve_shot_or_report(
    conn: Any,
    shot_code: str,
    parent: QtWidgets.QWidget | None,
) -> Any | None:
    try:
        return conn.get_shot_by_code(shot_code)
    except Exception as exc:
        log.error("Shot lookup failed for %s: %s", shot_code, exc, exc_info=True)
        MessageDialog(
            parent, f"Shot '{shot_code}' not found in ShotGrid.", "Playblast"
        ).exec_()
        return None


def _build_output_paths(
    context: HoudiniPlayblastLaunchContext,
) -> dict[Playblaster.PRESET, list[Path | str]]:
    output_paths: dict[Playblaster.PRESET, list[Path | str]] = {
        Playblaster.PRESET.EDIT_SQ: [context.output_base]
    }
    if context.custom_output_base is not None:
        output_paths[Playblaster.PRESET.EDIT_SQ].append(context.custom_output_base)
    return output_paths


def _run_local_playblast_or_report(
    playblaster: HPlayblaster,
    parent: QtWidgets.QWidget | None,
) -> bool:
    try:
        playblaster.playblast()
    except Exception as exc:
        log.error("Playblast failed: %s", exc, exc_info=True)
        MessageDialog(
            parent, "Playblast failed. Check the console for details.", "Playblast"
        ).exec_()
        return False
    return True


def _final_movie_path(output_base: str | Path, preset: Playblaster.PRESET) -> Path:
    return Path(str(output_base) + f".{preset.ext}")


def _show_success_dialog(
    parent: QtWidgets.QWidget | None,
    context: HoudiniPlayblastLaunchContext,
    final_primary_movie: Path,
) -> None:
    message = f"Playblast saved to:\n{final_primary_movie}"
    if context.custom_output_base is not None:
        custom_final_movie = _final_movie_path(
            context.custom_output_base,
            Playblaster.PRESET.EDIT_SQ,
        )
        message = f"{message}\n\nAdditional export:\n{custom_final_movie}"
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
    artist_display_name = resolve_artist_display_name().strip()
    if artist_display_name:
        log.info(
            "ShotGrid upload requested for %s by %s (not implemented yet).",
            movie_path,
            artist_display_name,
        )
    else:
        log.info("ShotGrid upload requested for %s (not implemented yet).", movie_path)
    MessageDialog(
        parent, "ShotGrid upload is not implemented yet.", "Playblast"
    ).exec_()
