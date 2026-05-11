"""Event capture and result recovery helpers for Substance Painter export."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import substance_painter as sp

from dcc.substance_painter.export.types import ExportEventSnapshot


def planned_export_count(exports_by_stack: dict[tuple[str, str], list[str]]) -> int:
    return sum(len(paths) for paths in exports_by_stack.values())


def normalize_texture_export_map(
    textures: object,
) -> dict[tuple[str, str], list[str]]:
    """Coerce an SP texture map into a consistently typed dict."""
    if not isinstance(textures, dict):
        return {}

    normalized: dict[tuple[str, str], list[str]] = {}
    for key, paths in textures.items():
        if not isinstance(key, tuple) or len(key) != 2 or not isinstance(paths, list):
            continue
        normalized[(str(key[0]), str(key[1]))] = [str(path) for path in paths]
    return normalized


def normalize_export_path(src_path: Path, export_path: str) -> Path:
    path = Path(export_path)
    if path.is_absolute():
        return path
    return src_path / path


def find_recent_written_exports(
    src_path: Path,
    planned_exports: dict[tuple[str, str], list[str]],
    *,
    started_at_unix: float,
) -> dict[tuple[str, str], list[str]]:
    """Scan disk for files written recently at planned export paths."""
    recovered: dict[tuple[str, str], list[str]] = {}
    for stack_key, export_paths in planned_exports.items():
        written_paths: list[str] = []
        for export_path in export_paths:
            resolved_path = normalize_export_path(src_path, export_path)
            try:
                stat = resolved_path.stat()
            except FileNotFoundError:
                continue
            if not resolved_path.is_file() or stat.st_size <= 0:
                continue
            if stat.st_mtime < started_at_unix - 5:
                continue
            written_paths.append(str(resolved_path))
        if written_paths:
            recovered[stack_key] = written_paths
    return recovered


def existing_source_file_count(src_path: Path) -> int:
    with suppress(FileNotFoundError):
        return sum(
            1 for path in src_path.iterdir() if path.is_file() and path.name != ".lock"
        )
    return 0


def capture_export_events() -> tuple[ExportEventSnapshot, Callable[[], None]]:
    """Capture ExportTexturesAboutToStart and ExportTexturesEnded into a snapshot."""
    snapshot = ExportEventSnapshot()

    def _on_about_to_start(event: sp.event.ExportTexturesAboutToStart) -> None:
        snapshot.about_to_start_textures = normalize_texture_export_map(
            getattr(event, "textures", None)
        )

    def _on_export_ended(event: sp.event.ExportTexturesEnded) -> None:
        snapshot.ended_status = getattr(event, "status", None)
        message = getattr(event, "message", "")
        snapshot.ended_message = str(message or "").strip() or None
        snapshot.ended_textures = normalize_texture_export_map(
            getattr(event, "textures", None)
        )

    sp.event.DISPATCHER.connect_strong(
        sp.event.ExportTexturesAboutToStart,
        _on_about_to_start,
    )
    sp.event.DISPATCHER.connect_strong(
        sp.event.ExportTexturesEnded,
        _on_export_ended,
    )

    def _disconnect() -> None:
        for event_type, callback in (
            (sp.event.ExportTexturesAboutToStart, _on_about_to_start),
            (sp.event.ExportTexturesEnded, _on_export_ended),
        ):
            with suppress(RuntimeError):
                sp.event.DISPATCHER.disconnect(event_type, callback)

    return snapshot, _disconnect


def resolve_exported_files(
    export_result: sp.export.TextureExportResult,
    planned_exports: dict[tuple[str, str], list[str]],
    event_snapshot: ExportEventSnapshot,
    *,
    started_at_unix: float,
    src_path: Path,
    logger: logging.Logger,
) -> dict[tuple[str, str], list[str]]:
    """Determine which texture files were actually written to disk."""
    returned_textures = {
        stack_key: list(export_paths)
        for stack_key, export_paths in normalize_texture_export_map(
            export_result.textures
        ).items()
        if export_paths
    }
    if returned_textures:
        return returned_textures

    ended_textures = {
        stack_key: list(export_paths)
        for stack_key, export_paths in (event_snapshot.ended_textures or {}).items()
        if export_paths
    }
    if ended_textures:
        logger.warning(
            "Substance export return was empty, but "
            f"ExportTexturesEnded reported {planned_export_count(ended_textures)} files."
        )
        return ended_textures

    recent_writes = find_recent_written_exports(
        src_path,
        planned_exports,
        started_at_unix=started_at_unix,
    )
    recovered_count = planned_export_count(recent_writes)
    planned_count = planned_export_count(planned_exports)

    ended_status = (
        getattr(event_snapshot.ended_status, "name", None)
        or str(event_snapshot.ended_status or "").strip()
    )
    result_message = str(getattr(export_result, "message", "") or "").strip() or None
    lock_path = src_path / ".lock"

    details = [
        "Substance Painter finished writing textures, but its export API did not "
        "report any exported files.",
        f"Planned files: {planned_count}",
        f"Recent files written to disk: {recovered_count}",
        "Existing files already in export folder before/after export: "
        f"{existing_source_file_count(src_path)}",
    ]
    about_to_start_count = planned_export_count(
        event_snapshot.about_to_start_textures or {}
    )
    if about_to_start_count:
        details.append(f"ExportTexturesAboutToStart planned: {about_to_start_count}")
    if ended_status:
        details.append(f"ExportTexturesEnded status: {ended_status}")
    if event_snapshot.ended_message:
        details.append(f"ExportTexturesEnded message: {event_snapshot.ended_message}")
    if result_message:
        details.append(f"export_project_textures() message: {result_message}")
    if lock_path.exists():
        details.append(f"Painter left export lock file behind: {lock_path}")
    raise RuntimeError("\n".join(details))
