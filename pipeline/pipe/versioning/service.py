"""High-level version workflows shared across DCC integrations."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Optional

from .model import VersionRecord, VersionStreamSpec
from .store import (
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
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str] = None,
) -> VersionRecord:
    """Copy a working file into backup storage and record a manual save."""
    normalized_stream = _normalize_stream(stream)
    normalized_title = _required_text(title, field_name="title")
    normalized_note = _optional_text(note)
    resolved_source = _resolve_existing_file(source_path, field_name="source_path")

    version = next_version(
        normalized_stream.backup_dir,
        normalized_stream.stem,
        normalized_stream.ext,
    )
    backup_path = backup_file(
        resolved_source,
        normalized_stream.backup_dir,
        stem=normalized_stream.stem,
        ext=normalized_stream.ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        raise RuntimeError(f"Failed to create backup for {resolved_source}")

    manifest = record_publish(
        normalized_stream.manifest_path,
        dcc=normalized_stream.dcc,
        source_path=resolved_source,
        backup_path=backup_path,
        version=version,
        title=normalized_title,
        context=_MANUAL_SAVE_CONTEXT,
        note=normalized_note,
        owner=normalized_stream.owner,
    )
    return _record_from_manifest(
        manifest=manifest,
        dcc=normalized_stream.dcc,
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
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str] = None,
) -> VersionRecord:
    """Copy an existing backup file into a new version slot."""
    normalized_stream = _normalize_stream(stream)
    normalized_title = _required_text(title, field_name="title")
    normalized_note = _optional_text(note)

    if record.backup_path is None:
        raise ValueError("Cannot promote version: selected record has no backup path.")

    source_backup = _resolve_record_backup_path(record.backup_path, normalized_stream)
    if not source_backup.exists() or not source_backup.is_file():
        raise ValueError(
            f"Cannot promote version: backup source does not exist: {source_backup}"
        )

    version = next_version(
        normalized_stream.backup_dir,
        normalized_stream.stem,
        normalized_stream.ext,
    )
    backup_path = backup_file(
        source_backup,
        normalized_stream.backup_dir,
        stem=normalized_stream.stem,
        ext=normalized_stream.ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        raise RuntimeError(f"Failed to promote backup file {source_backup}")

    manifest = record_publish(
        normalized_stream.manifest_path,
        dcc=normalized_stream.dcc,
        source_path=source_backup,
        backup_path=backup_path,
        version=version,
        title=normalized_title,
        context=_PROMOTED_CONTEXT,
        note=normalized_note,
        owner=normalized_stream.owner,
    )
    return _record_from_manifest(
        manifest=manifest,
        dcc=normalized_stream.dcc,
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


def list_version_records(stream: VersionStreamSpec) -> list[VersionRecord]:
    """Return version records newest-first, joined from manifest and filesystem."""
    normalized_stream = _normalize_stream(stream)

    manifest = load_manifest(normalized_stream.manifest_path)
    manifest_history = history_as_records(manifest, normalized_stream.dcc)
    records_by_version: dict[int, VersionRecord] = {}

    for record in manifest_history:
        stream_match = _record_for_stream(record, stream=normalized_stream)
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
        normalized_stream.backup_dir,
        normalized_stream.stem,
        normalized_stream.ext,
    ):
        if version in records_by_version:
            continue
        backup_path = normalized_stream.backup_dir / versioned_filename(
            normalized_stream.stem, normalized_stream.ext, version
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


def _normalize_stream(stream: VersionStreamSpec) -> VersionStreamSpec:
    return VersionStreamSpec(
        root_path=Path(stream.root_path).expanduser().resolve(),
        manifest_path=Path(stream.manifest_path).expanduser().resolve(),
        backup_dir=Path(stream.backup_dir).expanduser().resolve(),
        dcc=_required_text(stream.dcc, field_name="dcc"),
        stem=_required_text(stream.stem, field_name="stem"),
        ext=_required_ext(stream.ext),
        owner=stream.owner,
        label=_optional_text(stream.label),
        stream_key=_optional_text(stream.stream_key),
        working_path=(
            Path(stream.working_path).expanduser().resolve()
            if stream.working_path is not None
            else None
        ),
    )


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


def _resolve_record_backup_path(path: Path, stream: VersionStreamSpec) -> Path:
    if path.is_absolute():
        return path.expanduser().resolve()

    root_candidate = (stream.root_path / path).expanduser().resolve()
    if root_candidate.exists():
        return root_candidate

    backup_candidate = (stream.backup_dir / path.name).expanduser().resolve()
    if backup_candidate.exists():
        return backup_candidate

    return root_candidate


def _record_for_stream(
    record: VersionRecord,
    *,
    stream: VersionStreamSpec,
) -> tuple[int, Path] | None:
    backup_path = record.backup_path
    if backup_path is None:
        return None

    normalized_backup_path = _resolve_record_backup_path(backup_path, stream)
    parsed_version = _parse_version_from_name(
        stem=stream.stem,
        ext=stream.ext,
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
