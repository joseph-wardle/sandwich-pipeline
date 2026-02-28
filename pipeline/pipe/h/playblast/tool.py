from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import hou
from env_sg import DB_Config

from pipe.db import DB
from pipe.glui.dialogs import MessageDialog
from pipe.h import local
from pipe.playblast_artist import resolve_artist_display_name
from pipe.struct.db import Shot
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
    """Resolved inputs used by the Houdini playblast launch flow."""

    source_mode: Literal["shot", "custom"]
    shot_code: str | None
    custom_camera_path: str | None
    custom_frame_range: tuple[int, int] | None
    custom_shot_code: str
    output_bases: tuple[Path, ...]
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

    dialog = HPlayblastDialog(parent, conn, default_shot_code)
    if not dialog.exec_():
        return

    context = _build_launch_context_or_report(dialog, parent)
    if context is None:
        return

    shot = _resolve_source_shot_or_report(conn, context, parent)
    if shot is None:
        return

    out_paths = _build_output_paths(context)
    playblaster = HPlayblaster().configure(
        shot,
        out_paths,
        camera_path=context.custom_camera_path,
    )
    if not _run_local_playblast_or_report(playblaster, parent):
        return

    final_movies = _final_movie_paths(context.output_bases, Playblaster.PRESET.EDIT_SQ)
    primary_movie = final_movies[0]
    if context.source_mode == "shot" and context.upload_to_shotgrid:
        _upload_stub(parent, primary_movie)

    _show_success_dialog(parent, final_movies)


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
    output_bases = tuple(dialog.resolve_selected_output_bases())
    if not output_bases:
        MessageDialog(parent, "Unable to build export path.", "Playblast").exec_()
        return None

    source_mode = dialog.selected_source_mode
    shot_code = dialog.shot_code if source_mode == "shot" else None
    if source_mode == "shot" and not shot_code:
        MessageDialog(
            parent, "No shot code was found for Shot Playblast.", "Playblast"
        ).exec_()
        return None

    custom_frame_range = dialog.custom_frame_range if source_mode == "custom" else None

    return HoudiniPlayblastLaunchContext(
        source_mode=source_mode,
        shot_code=shot_code,
        custom_camera_path=dialog.custom_camera_path,
        custom_frame_range=custom_frame_range,
        custom_shot_code=dialog.custom_shot_code,
        output_bases=output_bases,
        upload_to_shotgrid=dialog.upload_to_shotgrid,
    )


def _resolve_source_shot_or_report(
    conn: Any,
    context: HoudiniPlayblastLaunchContext,
    parent: QtWidgets.QWidget | None,
) -> Shot | None:
    if context.source_mode == "custom":
        return _build_custom_mode_shot(context)

    shot_code = context.shot_code or ""
    try:
        return conn.get_shot_by_code(shot_code)
    except Exception as exc:
        log.error("Shot lookup failed for %s: %s", shot_code, exc, exc_info=True)
        MessageDialog(
            parent, f"Shot '{shot_code}' not found in ShotGrid.", "Playblast"
        ).exec_()
        return None


def _build_custom_mode_shot(context: HoudiniPlayblastLaunchContext) -> Shot | None:
    if context.custom_frame_range is None:
        return None

    cut_in, cut_out = context.custom_frame_range
    if cut_out < cut_in:
        cut_out = cut_in

    return Shot(
        code=context.custom_shot_code,
        id=0,
        assets=[],
        cut_in=cut_in,
        cut_out=cut_out,
        cut_duration=max(0, cut_out - cut_in),
        sequence=None,
        set=None,
        sets=[],
    )


def _build_output_paths(
    context: HoudiniPlayblastLaunchContext,
) -> dict[Playblaster.PRESET, list[Path | str]]:
    return {Playblaster.PRESET.EDIT_SQ: list(context.output_bases)}


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


def _final_movie_paths(
    output_bases: tuple[Path, ...],
    preset: Playblaster.PRESET,
) -> list[Path]:
    return [_final_movie_path(base, preset) for base in output_bases]


def _show_success_dialog(
    parent: QtWidgets.QWidget | None, final_movies: list[Path]
) -> None:
    if not final_movies:
        message = "Playblast export completed, but no output files were resolved."
        MessageDialog(parent, message, "Playblast").exec_()
        return

    message_lines = ["Playblast saved to:"]
    message_lines.extend(str(path) for path in final_movies)
    message = "\n".join(message_lines)
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
