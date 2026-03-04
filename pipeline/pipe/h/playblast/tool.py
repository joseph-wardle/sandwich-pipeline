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
from pipe.playblast_shotgrid import (
    UPLOAD_TARGET_REVIEW,
    UPLOAD_TARGET_VERSION_ONLY,
    PlayblastVersionUploadRequest,
    default_version_name_from_movie_path,
    resolve_preferred_upload_movie_path,
    upload_playblast_version,
)
from pipe.struct.db import Shot
from pipe.util import Playblaster

from .playblaster import HPlayblaster
from .ui import HPlayblastDialog

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from Qt import QtWidgets

SHOT_CODE_FALLBACK_PATTERN = re.compile(r"[A-Za-z]+_\d{3}(?:_[A-Za-z0-9]+)*")


@dataclass(frozen=True)
class HoudiniPlayblastLaunchContext:
    """Resolved inputs used by the Houdini playblast launch flow."""

    source_mode: Literal["shot", "custom"]
    shot_code: str | None
    custom_camera_path: str | None
    custom_frame_range: tuple[int, int] | None
    custom_shot_code: str
    output_destinations: tuple["ResolvedOutputDestination", ...]
    shotgrid_description: str
    upload_to_shotgrid: bool
    shotgrid_upload_target: str
    shotgrid_review_playlist_id: int | None
    shotgrid_review_load_error: str | None


@dataclass(frozen=True)
class ResolvedOutputDestination:
    """Resolved output base path paired with its destination label."""

    destination_name: str
    output_base: Path


@dataclass(frozen=True)
class HoudiniPlayblastExportConfig:
    """Fully resolved export configuration used by launch orchestration."""

    context: HoudiniPlayblastLaunchContext
    shot: Shot
    out_paths: dict[Playblaster.PRESET, list[Path | str]]
    final_movies: tuple[Path, ...]


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

    export_config = _generate_export_config_or_report(dialog, conn, parent)
    if export_config is None:
        return

    validation_error = _validate_export_config(export_config)
    if validation_error:
        MessageDialog(parent, validation_error, "Playblast").exec_()
        return

    if not _run_local_playblast_or_report(export_config, parent):
        return

    try:
        post_export_messages = _run_post_export_actions(export_config)
    except Exception as exc:
        log.exception("Post-playblast actions failed")
        post_export_messages = [
            "Post-export actions failed. Local playblast files were still written.",
            f"Reason: {exc}",
        ]

    success_message = _build_success_message(
        output_paths=list(export_config.final_movies),
        post_export_messages=post_export_messages,
    )
    MessageDialog(parent, success_message, "Playblast").exec_()


def _resolve_connection_or_report(parent: QtWidgets.QWidget | None) -> Any | None:
    try:
        return DB.Get(DB_Config)
    except Exception as exc:
        log.error("ShotGrid connection failed: %s", exc, exc_info=True)
        MessageDialog(parent, "Could not connect to ShotGrid.", "Playblast").exec_()
        return None


def _generate_export_config_or_report(
    dialog: HPlayblastDialog,
    conn: Any,
    parent: QtWidgets.QWidget | None,
) -> HoudiniPlayblastExportConfig | None:
    try:
        return _generate_export_config(dialog, conn)
    except Exception as exc:
        log.exception("Playblast config generation failed")
        MessageDialog(
            parent,
            f"Could not generate playblast settings.\n\n{exc}",
            "Playblast Error",
        ).exec_()
        return None


def _generate_export_config(
    dialog: HPlayblastDialog,
    conn: Any,
) -> HoudiniPlayblastExportConfig:
    context = _build_launch_context(dialog)
    shot = _resolve_source_shot(conn, context)
    out_paths = _build_output_paths(context)
    final_movies = tuple(_ordered_final_movie_paths_for_upload(context))
    return HoudiniPlayblastExportConfig(
        context=context,
        shot=shot,
        out_paths=out_paths,
        final_movies=final_movies,
    )


def _build_launch_context(
    dialog: HPlayblastDialog,
) -> HoudiniPlayblastLaunchContext:
    output_bases_by_destination = dialog.resolve_output_bases_by_destination()
    if not output_bases_by_destination:
        raise ValueError("Unable to build export path.")

    output_destinations = tuple(
        ResolvedOutputDestination(
            destination_name=destination_name,
            output_base=output_bases_by_destination[destination_name],
        )
        for destination_name in HPlayblastDialog.DESTINATION_ORDER
        if destination_name in output_bases_by_destination
    )
    if not output_destinations:
        raise ValueError("Unable to build export path.")

    source_mode = dialog.selected_source_mode
    shot_code = dialog.shot_code if source_mode == "shot" else None
    if source_mode == "shot" and not shot_code:
        raise ValueError("No shot code was found for Shot Playblast.")

    custom_camera_path = dialog.custom_camera_path if source_mode == "custom" else None
    custom_frame_range = dialog.custom_frame_range if source_mode == "custom" else None

    return HoudiniPlayblastLaunchContext(
        source_mode=source_mode,
        shot_code=shot_code,
        custom_camera_path=custom_camera_path,
        custom_frame_range=custom_frame_range,
        custom_shot_code=dialog.custom_shot_code,
        output_destinations=output_destinations,
        shotgrid_description=dialog.shotgrid_description,
        upload_to_shotgrid=dialog.upload_to_shotgrid,
        shotgrid_upload_target=dialog.shotgrid_upload_target,
        shotgrid_review_playlist_id=dialog.shotgrid_review_playlist_id,
        shotgrid_review_load_error=dialog.shotgrid_review_load_error,
    )


def _resolve_source_shot(
    conn: Any,
    context: HoudiniPlayblastLaunchContext,
) -> Shot:
    if context.source_mode == "custom":
        custom_mode_shot = _build_custom_mode_shot(context)
        if custom_mode_shot is None:
            raise ValueError("Could not build custom shot context.")
        return custom_mode_shot

    shot_code = context.shot_code or ""
    try:
        return conn.get_shot_by_code(shot_code)
    except Exception as exc:
        log.error("Shot lookup failed for %s: %s", shot_code, exc, exc_info=True)
        raise ValueError(f"Shot '{shot_code}' not found in ShotGrid.") from exc


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
    return {
        Playblaster.PRESET.EDIT_SQ: [
            destination.output_base for destination in context.output_destinations
        ]
    }


def _validate_export_config(config: HoudiniPlayblastExportConfig) -> str | None:
    if not config.out_paths:
        return "No playblast outputs are configured."

    output_count = sum(len(paths) for paths in config.out_paths.values())
    if output_count < 1:
        return "No playblast outputs are configured."

    if not config.final_movies:
        return "No output movie paths were resolved for this export."

    return None


def _run_local_playblast_or_report(
    config: HoudiniPlayblastExportConfig,
    parent: QtWidgets.QWidget | None,
) -> bool:
    playblaster = HPlayblaster().configure(
        config.shot,
        config.out_paths,
        camera_path=config.context.custom_camera_path,
    )
    try:
        playblaster.playblast()
    except Exception as exc:
        log.exception("Playblast export failed")
        MessageDialog(
            parent,
            f"Playblast failed.\n\n{exc}",
            "Playblast Error",
        ).exec_()
        return False
    return True


def _final_movie_path(output_base: str | Path, preset: Playblaster.PRESET) -> Path:
    return Path(str(output_base) + f".{preset.ext}")


def _ordered_final_movie_paths_for_upload(
    context: HoudiniPlayblastLaunchContext,
) -> list[Path]:
    return [
        _final_movie_path(destination.output_base, Playblaster.PRESET.EDIT_SQ)
        for destination in context.output_destinations
    ]


def _preferred_edit_movie_paths_for_upload(
    context: HoudiniPlayblastLaunchContext,
) -> list[Path]:
    for destination in context.output_destinations:
        if destination.destination_name != HPlayblastDialog.DESTINATION_EDIT:
            continue
        return [_final_movie_path(destination.output_base, Playblaster.PRESET.EDIT_SQ)]
    return []


def _resolve_shotgrid_upload_movie_path(
    context: HoudiniPlayblastLaunchContext,
) -> Path | None:
    """Resolve upload path deterministically: prefer Edit, then destination order."""
    ordered_paths = _ordered_final_movie_paths_for_upload(context)
    preferred_paths = _preferred_edit_movie_paths_for_upload(context)
    return resolve_preferred_upload_movie_path(
        ordered_paths,
        preferred_paths=preferred_paths,
    )


def _run_post_export_actions(config: HoudiniPlayblastExportConfig) -> list[str]:
    context = config.context
    if context.source_mode != "shot" or not context.upload_to_shotgrid:
        return []

    upload_movie = _resolve_shotgrid_upload_movie_path(context)
    if upload_movie is None:
        log.warning(
            "ShotGrid upload requested but no valid movie output was found in selected destinations."
        )
        return ["ShotGrid Upload: Skipped - no valid playblast movie file was found."]

    return _upload_shot_playblast_to_shotgrid(context, upload_movie)


def _upload_shot_playblast_to_shotgrid(
    context: HoudiniPlayblastLaunchContext,
    movie_path: Path,
) -> list[str]:
    shot_code = str(context.shot_code or "").strip()
    if not shot_code:
        return ["ShotGrid Upload: Skipped - shot code is missing."]

    version_name = default_version_name_from_movie_path(movie_path)
    if not version_name:
        version_name = f"{shot_code}_playblast"

    artist_name = resolve_artist_display_name().strip() or None
    (
        upload_target,
        review_playlist_id,
        pre_upload_warning,
        fallback_reason,
        selected_playlist_id,
    ) = _resolve_upload_target_for_request(context)
    upload_request = PlayblastVersionUploadRequest(
        shot_code=shot_code,
        movie_path=movie_path,
        version_name=version_name,
        description=context.shotgrid_description or None,
        path_to_frames=str(movie_path),
        artist_display_name=artist_name,
        upload_target=upload_target,
        review_playlist_id=review_playlist_id,
    )

    try:
        upload_result = upload_playblast_version(upload_request)
    except Exception as exc:
        log.exception("ShotGrid upload failed for shot '%s'", shot_code)
        return [f"ShotGrid Upload: Failed - {exc}"]

    message_lines: list[str] = []
    if upload_result.ok:
        success_message = (
            f"ShotGrid Upload: Success - {upload_result.version_name}"
            f" (shot {upload_result.shot_code})."
        )
        if upload_result.version_id is not None:
            success_message = (
                f"{success_message} Version ID: {upload_result.version_id}."
            )
        message_lines.append(success_message)
    else:
        message_lines.append(f"ShotGrid Upload: Failed - {upload_result.message}")

    if pre_upload_warning and upload_result.ok:
        message_lines.append(f"ShotGrid Warning: {pre_upload_warning}")
    if pre_upload_warning:
        log.warning(
            "ShotGrid review upload fallback to version upload "
            "(shot_code=%s, version_id=%s, playlist_id=%s, reason=%s)",
            shot_code,
            upload_result.version_id,
            selected_playlist_id,
            fallback_reason or "review playlist unavailable",
        )
    for warning in upload_result.warnings:
        message_lines.append(f"ShotGrid Warning: {warning}")

    return message_lines


def _resolve_upload_target_for_request(
    context: HoudiniPlayblastLaunchContext,
) -> tuple[str, int | None, str | None, str | None, int | None]:
    normalized_target = str(context.shotgrid_upload_target or "").strip().lower()
    if normalized_target != UPLOAD_TARGET_REVIEW:
        return (UPLOAD_TARGET_VERSION_ONLY, None, None, None, None)

    playlist_id = context.shotgrid_review_playlist_id
    if isinstance(playlist_id, int) and playlist_id > 0:
        return (UPLOAD_TARGET_REVIEW, playlist_id, None, None, playlist_id)

    if context.shotgrid_review_load_error:
        return (
            UPLOAD_TARGET_VERSION_ONLY,
            None,
            "Review upload skipped because recent reviews could not be loaded. "
            "Version upload continued.",
            context.shotgrid_review_load_error,
            playlist_id,
        )

    return (
        UPLOAD_TARGET_VERSION_ONLY,
        None,
        "Review upload skipped because no valid review playlist was selected. "
        "Version upload continued.",
        "missing review playlist id",
        playlist_id,
    )


def _build_success_message(
    output_paths: list[Path],
    post_export_messages: list[str],
) -> str:
    message_lines = ["Local playblast export successful."]
    if output_paths:
        message_lines.append("")
        message_lines.append("Outputs:")
        message_lines.extend(str(path) for path in output_paths)
    if post_export_messages:
        message_lines.append("")
        message_lines.append("Post-export:")
        message_lines.extend(post_export_messages)
    return "\n".join(message_lines)


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
