"""High-level asset version workflows shared across DCC integrations.

This module provides thin orchestration around :mod:`pipe.asset.versioning` for:
1. Manual version saves.
2. Promoting an existing version as a new head version.
3. Listing version records joined from manifest + filesystem.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Optional

from .paths import AssetPaths
from .versioning import (
    VersionRecord,
    backup_file,
    history_as_records,
    list_versions,
    load_manifest,
    next_version,
    record_publish,
    versioned_filename,
)

_VERSION_RE_TEMPLATE = r"^{stem}\.v(?P<ver>\d+)\.{ext}$"
_MANUAL_SAVE_CONTEXT = "manual_save"
_PROMOTED_CONTEXT = "promoted"


def save_version(
    source_path: Path,
    asset_paths: AssetPaths,
    dcc: str,
    *,
    stem: str,
    ext: str,
    title: str,
    note: Optional[str] = None,
) -> VersionRecord:
    """Copy source to .backup and record a manual_save entry in the manifest."""
    normalized_dcc = _required_text(dcc, field_name="dcc")
    normalized_stem = _required_text(stem, field_name="stem")
    normalized_ext = _required_ext(ext)
    normalized_title = _required_text(title, field_name="title")
    normalized_note = _optional_text(note)

    resolved_source = _resolve_existing_file(source_path, field_name="source_path")
    version = next_version(asset_paths.backup_dir, normalized_stem, normalized_ext)
    backup_path = backup_file(
        resolved_source,
        asset_paths.backup_dir,
        stem=normalized_stem,
        ext=normalized_ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        raise RuntimeError(f"Failed to create backup for {resolved_source}")

    manifest = record_publish(
        asset_paths.manifest_path,
        dcc=normalized_dcc,
        source_path=resolved_source,
        backup_path=backup_path,
        version=version,
        title=normalized_title,
        context=_MANUAL_SAVE_CONTEXT,
        note=normalized_note,
    )
    return _record_from_manifest(
        manifest=manifest,
        dcc=normalized_dcc,
        version=version,
        backup_path=backup_path,
        fallback=VersionRecord(
            version=version,
            title=normalized_title,
            note=normalized_note,
            context=_MANUAL_SAVE_CONTEXT,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=str(resolved_source),
        ),
    )


def promote_version(
    record: VersionRecord,
    asset_paths: AssetPaths,
    dcc: str,
    *,
    stem: str,
    ext: str,
    title: str,
    note: Optional[str] = None,
) -> VersionRecord:
    """Copy an old backup file as the new head version, context="promoted"."""
    normalized_dcc = _required_text(dcc, field_name="dcc")
    normalized_stem = _required_text(stem, field_name="stem")
    normalized_ext = _required_ext(ext)
    normalized_title = _required_text(title, field_name="title")
    normalized_note = _optional_text(note)

    if record.backup_path is None:
        raise ValueError("Cannot promote version: selected record has no backup path.")

    source_backup = _resolve_record_backup_path(record.backup_path, asset_paths)
    if not source_backup.exists() or not source_backup.is_file():
        raise ValueError(
            f"Cannot promote version: backup source does not exist: {source_backup}"
        )

    version = next_version(asset_paths.backup_dir, normalized_stem, normalized_ext)
    backup_path = backup_file(
        source_backup,
        asset_paths.backup_dir,
        stem=normalized_stem,
        ext=normalized_ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        raise RuntimeError(f"Failed to promote backup file {source_backup}")

    manifest = record_publish(
        asset_paths.manifest_path,
        dcc=normalized_dcc,
        source_path=source_backup,
        backup_path=backup_path,
        version=version,
        title=normalized_title,
        context=_PROMOTED_CONTEXT,
        note=normalized_note,
    )
    return _record_from_manifest(
        manifest=manifest,
        dcc=normalized_dcc,
        version=version,
        backup_path=backup_path,
        fallback=VersionRecord(
            version=version,
            title=normalized_title,
            note=normalized_note,
            context=_PROMOTED_CONTEXT,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=str(source_backup),
        ),
    )


def list_version_records(
    asset_paths: AssetPaths,
    dcc: str,
    stem: str,
    ext: str,
) -> list[VersionRecord]:
    """Return version records newest-first, joined from filesystem + manifest."""
    normalized_dcc = _required_text(dcc, field_name="dcc")
    normalized_stem = _required_text(stem, field_name="stem")
    normalized_ext = _required_ext(ext)

    manifest = load_manifest(asset_paths.manifest_path)
    manifest_history = history_as_records(manifest, normalized_dcc)
    records_by_version: dict[int, VersionRecord] = {}

    for record in manifest_history:
        stream_match = _record_for_stream(
            record,
            asset_paths=asset_paths,
            stem=normalized_stem,
            ext=normalized_ext,
        )
        if stream_match is None:
            continue
        version, normalized_backup_path = stream_match
        if version in records_by_version:
            continue
        records_by_version[version] = replace(
            record,
            version=version,
            backup_path=normalized_backup_path,
        )

    for version in list_versions(
        asset_paths.backup_dir, normalized_stem, normalized_ext
    ):
        if version in records_by_version:
            continue
        backup_path = asset_paths.backup_dir / versioned_filename(
            normalized_stem, normalized_ext, version
        )
        records_by_version[version] = VersionRecord(
            version=version,
            title=None,
            note=None,
            context=None,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=None,
        )

    return [records_by_version[v] for v in sorted(records_by_version, reverse=True)]


def _required_text(value: object, *, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{field_name} is required.")
    return text


def _required_ext(ext: str) -> str:
    normalized = _required_text(ext, field_name="ext").lstrip(".")
    if not normalized:
        raise ValueError("ext is required.")
    return normalized


def _optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_existing_file(path: Path, *, field_name: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"{field_name} does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"{field_name} is not a file: {resolved}")
    return resolved


def _resolve_record_backup_path(path: Path, asset_paths: AssetPaths) -> Path:
    if path.is_absolute():
        return path.expanduser().resolve()

    root_candidate = (asset_paths.root / path).expanduser().resolve()
    if root_candidate.exists():
        return root_candidate

    backup_candidate = (asset_paths.backup_dir / path.name).expanduser().resolve()
    if backup_candidate.exists():
        return backup_candidate

    return root_candidate


def _record_for_stream(
    record: VersionRecord,
    *,
    asset_paths: AssetPaths,
    stem: str,
    ext: str,
) -> tuple[int, Path] | None:
    backup_path = record.backup_path
    if backup_path is None:
        return None

    normalized_backup_path = _resolve_record_backup_path(backup_path, asset_paths)
    parsed_version = _parse_version_from_name(
        stem=stem,
        ext=ext,
        filename=normalized_backup_path.name,
    )
    if parsed_version is None:
        return None
    return parsed_version, normalized_backup_path


def _parse_version_from_name(*, stem: str, ext: str, filename: str) -> int | None:
    pattern = _VERSION_RE_TEMPLATE.format(stem=re.escape(stem), ext=re.escape(ext))
    match = re.match(pattern, filename)
    if not match:
        return None
    try:
        return int(match.group("ver"))
    except Exception:
        return None


def _record_from_manifest(
    *,
    manifest: dict[str, object],
    dcc: str,
    version: int,
    backup_path: Path,
    fallback: VersionRecord,
) -> VersionRecord:
    for record in history_as_records(manifest, dcc):
        if record.version != version:
            continue
        if record.backup_path is None:
            continue
        if record.backup_path == backup_path:
            return record
    return fallback


__all__ = [
    "list_version_records",
    "promote_version",
    "save_version",
]
