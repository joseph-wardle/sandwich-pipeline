"""High-level version workflows shared across DCC integrations."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Optional

from .model import (
    VersionRecord,
    VersionSnapshotMember,
    VersionStreamSpec,
    stream_filename,
    stream_key_for,
)
from .store import (
    _BUNDLE_VERSION_RE,
    _parse_version_from_name,
    backup_file,
    bundle_dirname,
    history_as_records,
    list_bundle_versions,
    list_versions,
    load_manifest,
    next_bundle_version,
    next_version,
    record_publish,
    versioned_filename,
)
_MANUAL_SAVE_CONTEXT = "manual_save"
_PROMOTED_CONTEXT = "promoted"


def save_version(
    source_path: Path,
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str] = None,
) -> VersionRecord:
    """Copy the current working state into version storage."""
    normalized_stream = _normalize_stream(stream)
    normalized_title = _required_text(title, field_name="title")
    normalized_note = _optional_text(note)

    if _is_compound_stream(normalized_stream):
        return _save_compound_version(
            source_path,
            normalized_stream,
            title=normalized_title,
            note=normalized_note,
        )

    return _save_single_file_version(
        source_path,
        normalized_stream,
        title=normalized_title,
        note=normalized_note,
    )


def promote_version(
    record: VersionRecord,
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str] = None,
) -> VersionRecord:
    """Copy an existing version into a new version slot."""
    normalized_stream = _normalize_stream(stream)
    normalized_title = _required_text(title, field_name="title")
    normalized_note = _optional_text(note)

    if _is_compound_stream(normalized_stream):
        return _promote_compound_version(
            record,
            normalized_stream,
            title=normalized_title,
            note=normalized_note,
        )

    return _promote_single_file_version(
        record,
        normalized_stream,
        title=normalized_title,
        note=normalized_note,
    )


def list_version_records(stream: VersionStreamSpec) -> list[VersionRecord]:
    """Return version records newest-first, joined from manifest and filesystem."""
    normalized_stream = _normalize_stream(stream)
    if _is_compound_stream(normalized_stream):
        return _list_compound_version_records(normalized_stream)
    return _list_single_file_version_records(normalized_stream)


def _save_single_file_version(
    source_path: Path,
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str],
) -> VersionRecord:
    assert stream.stream_key is not None
    resolved_source = _resolve_existing_file(source_path, field_name="source_path")

    version = next_version(stream.backup_dir, stream.stem, stream.ext)
    backup_path = backup_file(
        resolved_source,
        stream.backup_dir,
        stem=stream.stem,
        ext=stream.ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        raise RuntimeError(f"Failed to create backup for {resolved_source}")

    manifest = record_publish(
        stream.manifest_path,
        dcc=stream.dcc,
        stream_key=stream.stream_key,
        stem=stream.stem,
        ext=stream.ext,
        stream_label=stream.label,
        working_path=stream.working_path,
        source_path=resolved_source,
        backup_path=backup_path,
        version=version,
        title=title,
        context=_MANUAL_SAVE_CONTEXT,
        note=note,
        owner=stream.owner,
    )
    return _record_from_manifest(
        manifest=manifest,
        stream_key=stream.stream_key,
        fallback_dcc=stream.dcc,
        version=version,
        backup_path=backup_path,
        fallback=VersionRecord(
            version=version,
            title=title,
            note=note,
            context=_MANUAL_SAVE_CONTEXT,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=str(resolved_source),
        ),
    )


def _promote_single_file_version(
    record: VersionRecord,
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str],
) -> VersionRecord:
    assert stream.stream_key is not None
    if record.backup_path is None:
        raise ValueError("Cannot promote version: selected record has no backup path.")

    source_backup = _resolve_record_backup_path(record.backup_path, stream)
    if not source_backup.exists() or not source_backup.is_file():
        raise ValueError(
            f"Cannot promote version: backup source does not exist: {source_backup}"
        )

    version = next_version(stream.backup_dir, stream.stem, stream.ext)
    backup_path = backup_file(
        source_backup,
        stream.backup_dir,
        stem=stream.stem,
        ext=stream.ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        raise RuntimeError(f"Failed to promote backup file {source_backup}")

    manifest = record_publish(
        stream.manifest_path,
        dcc=stream.dcc,
        stream_key=stream.stream_key,
        stem=stream.stem,
        ext=stream.ext,
        stream_label=stream.label,
        working_path=stream.working_path,
        source_path=source_backup,
        backup_path=backup_path,
        version=version,
        title=title,
        context=_PROMOTED_CONTEXT,
        note=note,
        owner=stream.owner,
    )
    return _record_from_manifest(
        manifest=manifest,
        stream_key=stream.stream_key,
        fallback_dcc=stream.dcc,
        version=version,
        backup_path=backup_path,
        fallback=VersionRecord(
            version=version,
            title=title,
            note=note,
            context=_PROMOTED_CONTEXT,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=str(source_backup),
        ),
    )


def _list_single_file_version_records(stream: VersionStreamSpec) -> list[VersionRecord]:
    assert stream.stream_key is not None
    manifest = load_manifest(stream.manifest_path)
    manifest_history = history_as_records(
        manifest,
        stream.stream_key,
        fallback_dcc=stream.dcc,
    )
    records_by_version: dict[int, VersionRecord] = {}

    for record in manifest_history:
        stream_match = _single_file_record_for_stream(record, stream=stream)
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

    for version in list_versions(stream.backup_dir, stream.stem, stream.ext):
        if version in records_by_version:
            continue
        backup_path = stream.backup_dir / versioned_filename(
            stream.stem, stream.ext, version
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


def _save_compound_version(
    source_path: Path,
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str],
) -> VersionRecord:
    assert stream.stream_key is not None
    resolved_source = _resolve_existing_file(source_path, field_name="source_path")
    source_root = _resolve_snapshot_source_root(resolved_source, stream)

    version = next_bundle_version(stream.backup_dir)
    backup_root, backup_path, backup_members = _backup_compound_snapshot(
        source_root,
        stream,
        version=version,
    )

    manifest = record_publish(
        stream.manifest_path,
        dcc=stream.dcc,
        stream_key=stream.stream_key,
        stem=stream.stem,
        ext=stream.ext,
        stream_label=stream.label,
        working_path=stream.working_path,
        source_path=resolved_source,
        backup_path=backup_path,
        backup_root=backup_root,
        backup_members=list(backup_members),
        version=version,
        title=title,
        context=_MANUAL_SAVE_CONTEXT,
        note=note,
        owner=stream.owner,
    )
    return _record_from_manifest(
        manifest=manifest,
        stream_key=stream.stream_key,
        fallback_dcc=stream.dcc,
        version=version,
        backup_path=backup_path,
        fallback=VersionRecord(
            version=version,
            title=title,
            note=note,
            context=_MANUAL_SAVE_CONTEXT,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=str(resolved_source),
            backup_root=backup_root,
            backup_members=backup_members,
        ),
    )


def _promote_compound_version(
    record: VersionRecord,
    stream: VersionStreamSpec,
    *,
    title: str,
    note: Optional[str],
) -> VersionRecord:
    assert stream.stream_key is not None
    source_root = _resolve_record_backup_root(record, stream)
    if source_root is None:
        raise ValueError(
            "Cannot promote version: selected record has no compound backup root."
        )

    source_backup = _resolve_compound_record_backup_path(record, stream, source_root)
    if not source_backup.exists() or not source_backup.is_file():
        raise ValueError(
            f"Cannot promote version: backup source does not exist: {source_backup}"
        )

    version = next_bundle_version(stream.backup_dir)
    backup_root, backup_path, backup_members = _backup_compound_snapshot(
        source_root,
        stream,
        version=version,
        member_paths=_record_member_paths(record, stream),
    )

    manifest = record_publish(
        stream.manifest_path,
        dcc=stream.dcc,
        stream_key=stream.stream_key,
        stem=stream.stem,
        ext=stream.ext,
        stream_label=stream.label,
        working_path=stream.working_path,
        source_path=source_backup,
        backup_path=backup_path,
        backup_root=backup_root,
        backup_members=list(backup_members),
        version=version,
        title=title,
        context=_PROMOTED_CONTEXT,
        note=note,
        owner=stream.owner,
    )
    return _record_from_manifest(
        manifest=manifest,
        stream_key=stream.stream_key,
        fallback_dcc=stream.dcc,
        version=version,
        backup_path=backup_path,
        fallback=VersionRecord(
            version=version,
            title=title,
            note=note,
            context=_PROMOTED_CONTEXT,
            user=None,
            timestamp=None,
            backup_path=backup_path,
            source_file=str(source_backup),
            backup_root=backup_root,
            backup_members=backup_members,
        ),
    )


def _list_compound_version_records(stream: VersionStreamSpec) -> list[VersionRecord]:
    assert stream.stream_key is not None
    manifest = load_manifest(stream.manifest_path)
    manifest_history = history_as_records(
        manifest,
        stream.stream_key,
        fallback_dcc=stream.dcc,
    )
    records_by_version: dict[int, VersionRecord] = {}

    for record in manifest_history:
        stream_match = _compound_record_for_stream(record, stream=stream)
        if stream_match is None:
            continue
        version, backup_path, backup_root, backup_members = stream_match
        if version in records_by_version:
            continue
        records_by_version[version] = replace(
            record,
            version=version,
            backup_path=backup_path,
            backup_root=backup_root,
            backup_members=backup_members,
        )

    for version in list_bundle_versions(stream.backup_dir):
        if version in records_by_version:
            continue
        bundle_root = stream.backup_dir / bundle_dirname(version)
        primary_backup = bundle_root / _primary_snapshot_member(stream).relative_path
        backup_members = tuple(
            member.relative_path.as_posix()
            for member in stream.snapshot_members
            if (bundle_root / member.relative_path).exists()
        )
        if not backup_members:
            backup_members = tuple(
                member.relative_path.as_posix() for member in stream.snapshot_members
            )
        records_by_version[version] = VersionRecord(
            version=version,
            title=None,
            note=None,
            context=None,
            user=None,
            timestamp=None,
            backup_path=primary_backup,
            source_file=None,
            backup_root=bundle_root,
            backup_members=backup_members,
        )

    return [records_by_version[v] for v in sorted(records_by_version, reverse=True)]


def _normalize_stream(stream: VersionStreamSpec) -> VersionStreamSpec:
    normalized_root_path = Path(stream.root_path).expanduser().resolve()
    normalized_dcc = _required_text(stream.dcc, field_name="dcc")
    normalized_stem = _required_text(stream.stem, field_name="stem")
    normalized_ext = _required_ext(stream.ext)
    normalized_working_path = (
        Path(stream.working_path).expanduser().resolve()
        if stream.working_path is not None
        else None
    )
    normalized_members = _normalize_snapshot_members(
        normalized_root_path,
        stream.snapshot_members,
        working_path=normalized_working_path,
    )
    return VersionStreamSpec(
        root_path=normalized_root_path,
        manifest_path=Path(stream.manifest_path).expanduser().resolve(),
        backup_dir=Path(stream.backup_dir).expanduser().resolve(),
        dcc=normalized_dcc,
        stem=normalized_stem,
        ext=normalized_ext,
        owner=stream.owner,
        label=_optional_text(stream.label)
        or stream_filename(normalized_stem, normalized_ext),
        stream_key=_optional_text(stream.stream_key)
        or stream_key_for(normalized_dcc, normalized_stem, normalized_ext),
        working_path=normalized_working_path,
        snapshot_members=normalized_members,
    )


def _normalize_snapshot_members(
    root_path: Path,
    members: tuple[VersionSnapshotMember, ...],
    *,
    working_path: Path | None,
) -> tuple[VersionSnapshotMember, ...]:
    if not members:
        return ()

    working_relative: Path | None = None
    if working_path is not None:
        try:
            working_relative = working_path.relative_to(root_path)
        except Exception:
            working_relative = None

    normalized_members: list[VersionSnapshotMember] = []
    primary_index: int | None = None

    for index, member in enumerate(members):
        relative_path = Path(member.relative_path)
        if relative_path.is_absolute():
            try:
                relative_path = relative_path.resolve().relative_to(root_path)
            except Exception as exc:
                raise ValueError(
                    f"Snapshot member must live under the stream root: {relative_path}"
                ) from exc
        if relative_path == Path(".") or any(
            part == ".." for part in relative_path.parts
        ):
            raise ValueError(
                f"Snapshot member must use a clean relative path: {relative_path}"
            )

        normalized_member = VersionSnapshotMember(
            relative_path=relative_path,
            label=_optional_text(member.label),
            primary=bool(member.primary),
            required=bool(member.required),
        )
        if normalized_member.primary:
            if primary_index is not None:
                raise ValueError("Compound stream may only declare one primary member.")
            primary_index = index
        normalized_members.append(normalized_member)

    if primary_index is None and working_relative is not None:
        for index, member in enumerate(normalized_members):
            if member.relative_path == working_relative:
                normalized_members[index] = replace(member, primary=True)
                primary_index = index
                break

    if primary_index is None:
        normalized_members[0] = replace(normalized_members[0], primary=True)

    return tuple(normalized_members)


def _is_compound_stream(stream: VersionStreamSpec) -> bool:
    return bool(stream.snapshot_members)


def _primary_snapshot_member(stream: VersionStreamSpec) -> VersionSnapshotMember:
    for member in stream.snapshot_members:
        if member.primary:
            return member
    raise ValueError("Compound stream requires a primary snapshot member.")


def _backup_compound_snapshot(
    source_root: Path,
    stream: VersionStreamSpec,
    *,
    version: int,
    member_paths: tuple[str, ...] | None = None,
) -> tuple[Path, Path, tuple[str, ...]]:
    backup_root = stream.backup_dir / bundle_dirname(version)
    if backup_root.exists():
        raise RuntimeError(f"Version bundle already exists: {backup_root}")

    backup_root.mkdir(parents=True, exist_ok=True)
    copied_members: list[str] = []
    primary_backup_path: Path | None = None

    allowed_members = set(member_paths or _record_member_paths(None, stream))

    try:
        for member in stream.snapshot_members:
            relative_key = member.relative_path.as_posix()
            if relative_key not in allowed_members:
                continue

            source_member_path = (
                (source_root / member.relative_path).expanduser().resolve()
            )
            if not source_member_path.exists():
                if member.required:
                    raise ValueError(
                        f"Missing required snapshot file: {source_member_path}"
                    )
                continue
            if not source_member_path.is_file():
                raise ValueError(f"Snapshot member is not a file: {source_member_path}")

            target_path = backup_root / member.relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target_path.with_name(f".{target_path.name}.tmp")
            shutil.copy2(source_member_path, temp_path)
            os.replace(temp_path, target_path)

            copied_members.append(relative_key)
            if member.primary:
                primary_backup_path = target_path

        if primary_backup_path is None:
            raise ValueError("Compound snapshot is missing its primary file.")
    except Exception:
        shutil.rmtree(backup_root, ignore_errors=True)
        raise

    return backup_root, primary_backup_path, tuple(copied_members)


def _resolve_snapshot_source_root(source_path: Path, stream: VersionStreamSpec) -> Path:
    for member in stream.snapshot_members:
        working_candidate = (stream.root_path / member.relative_path).resolve()
        if source_path == working_candidate:
            return stream.root_path

    try:
        relative_to_backup = source_path.relative_to(stream.backup_dir)
    except Exception as exc:
        raise ValueError(
            f"source_path is not part of this compound stream: {source_path}"
        ) from exc

    if len(relative_to_backup.parts) < 2:
        raise ValueError(f"source_path is not inside a version bundle: {source_path}")

    bundle_name = relative_to_backup.parts[0]
    member_path = Path(*relative_to_backup.parts[1:])
    if _parse_bundle_version(bundle_name) is None:
        raise ValueError(f"source_path is not inside a version bundle: {source_path}")
    if member_path not in {member.relative_path for member in stream.snapshot_members}:
        raise ValueError(
            f"source_path is not part of this compound stream: {source_path}"
        )
    return (stream.backup_dir / bundle_name).resolve()


def _record_member_paths(
    record: VersionRecord | None,
    stream: VersionStreamSpec,
) -> tuple[str, ...]:
    if record is not None and record.backup_members:
        return record.backup_members
    return tuple(member.relative_path.as_posix() for member in stream.snapshot_members)


def _single_file_record_for_stream(
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
        name=normalized_backup_path.name,
    )
    if parsed_version is None:
        return None
    return parsed_version, normalized_backup_path


def _compound_record_for_stream(
    record: VersionRecord,
    *,
    stream: VersionStreamSpec,
) -> tuple[int, Path, Path, tuple[str, ...]] | None:
    backup_root = _resolve_record_backup_root(record, stream)
    if backup_root is None:
        return None

    version = _parse_bundle_version(backup_root.name)
    if version is None:
        return None

    backup_path = _resolve_compound_record_backup_path(record, stream, backup_root)
    backup_members = _record_member_paths(record, stream)
    return version, backup_path, backup_root, backup_members


def _resolve_record_backup_path(path: Path, stream: VersionStreamSpec) -> Path:
    if path.is_absolute():
        return path.expanduser().resolve()

    root_candidate = (stream.root_path / path).expanduser().resolve()
    if root_candidate.exists():
        return root_candidate

    backup_candidate = (stream.backup_dir / path).expanduser().resolve()
    if backup_candidate.exists():
        return backup_candidate

    return backup_candidate


def _resolve_record_backup_root(
    record: VersionRecord,
    stream: VersionStreamSpec,
) -> Path | None:
    if record.backup_root is not None:
        backup_root = record.backup_root
        if backup_root.is_absolute():
            return backup_root.expanduser().resolve()

        root_candidate = (stream.root_path / backup_root).expanduser().resolve()
        if root_candidate.exists():
            return root_candidate

        backup_candidate = (stream.backup_dir / backup_root).expanduser().resolve()
        if backup_candidate.exists():
            return backup_candidate

        return backup_candidate

    if record.backup_path is None:
        return None

    resolved_backup_path = _resolve_record_backup_path(record.backup_path, stream)
    try:
        relative_to_backup = resolved_backup_path.relative_to(stream.backup_dir)
    except Exception:
        return None

    if len(relative_to_backup.parts) < 2:
        return None

    bundle_name = relative_to_backup.parts[0]
    if _parse_bundle_version(bundle_name) is None:
        return None
    return (stream.backup_dir / bundle_name).resolve()


def _resolve_compound_record_backup_path(
    record: VersionRecord,
    stream: VersionStreamSpec,
    backup_root: Path,
) -> Path:
    if record.backup_path is not None:
        resolved_backup_path = _resolve_record_backup_path(record.backup_path, stream)
        try:
            relative_to_bundle = resolved_backup_path.relative_to(backup_root)
        except Exception:
            relative_to_bundle = None
        if relative_to_bundle is not None and relative_to_bundle in {
            member.relative_path for member in stream.snapshot_members
        }:
            return resolved_backup_path
    return (backup_root / _primary_snapshot_member(stream).relative_path).resolve()


def _parse_bundle_version(dirname: str) -> int | None:
    match = _BUNDLE_VERSION_RE.match(dirname)
    if not match:
        return None
    try:
        return int(match.group("ver"))
    except Exception:
        return None


def _record_from_manifest(
    *,
    manifest: dict[str, object],
    stream_key: str,
    fallback_dcc: str,
    version: int,
    backup_path: Path,
    fallback: VersionRecord,
) -> VersionRecord:
    for record in history_as_records(
        manifest,
        stream_key,
        fallback_dcc=fallback_dcc,
    ):
        if record.version != version:
            continue
        if record.backup_path is None:
            continue
        if record.backup_path == backup_path:
            return record
    return fallback


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


def path_matches_stream(path: Path, stream: VersionStreamSpec) -> bool:
    """Return ``True`` when a file path belongs to a stream's working or backup set.

    Checks three locations in order:
    1. Any snapshot member's working location under the stream root.
    2. The stream's own working path.
    3. The stream's backup directory (single-file or compound bundle).
    """
    resolved_path = Path(path).expanduser().resolve()

    # Check snapshot member working locations
    for member in stream.snapshot_members:
        if resolved_path == (Path(stream.root_path) / member.relative_path).resolve():
            return True

    # Check the primary working path
    if stream.working_path is not None:
        if resolved_path == Path(stream.working_path).expanduser().resolve():
            return True

    # Check inside the backup directory
    resolved_backup_dir = Path(stream.backup_dir).expanduser().resolve()
    try:
        relative_to_backup = resolved_path.relative_to(resolved_backup_dir)
    except Exception:
        return False

    if stream.snapshot_members:
        # Compound stream: path must be <backup_dir>/v###/<member_relative_path>
        if len(relative_to_backup.parts) < 2:
            return False
        bundle_name = relative_to_backup.parts[0]
        if not _BUNDLE_VERSION_RE.match(bundle_name):
            return False
        member_path = Path(*relative_to_backup.parts[1:])
        return member_path in {
            member.relative_path for member in stream.snapshot_members
        }

    # Single-file stream: path must be a versioned file directly in the backup dir
    return (
        resolved_path.parent == resolved_backup_dir
        and resolved_path.suffix.lower() == f".{stream.ext.lower()}"
    )


__all__ = [
    "list_version_records",
    "path_matches_stream",
    "promote_version",
    "save_version",
]
