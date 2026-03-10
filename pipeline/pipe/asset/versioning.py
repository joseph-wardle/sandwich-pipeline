"""Asset compatibility wrappers around the shared versioning core.

The implementation now lives in :mod:`pipe.versioning`. This module keeps the
asset-facing API stable while the wider pipeline migrates to the shared package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pipe.versioning import (
    BackupResult,
    VersionOwner,
    VersionRecord,
    stream_key_for,
    version_label,
)
from pipe.versioning.store import (
    backup_file,
    backup_if_changed as _backup_if_changed,
    build_manifest as _build_manifest,
    compute_signature,
    current_record,
    get_manifest_path,
    history_as_records,
    list_versions,
    load_manifest,
    next_version,
    record_publish as _record_publish,
    save_manifest,
    versioned_filename,
)

from .version_adapter import asset_owner_from_metadata


@dataclass(frozen=True)
class _AssetStreamIdentity:
    stream_key: str
    stem: str
    ext: str
    stream_label: str
    working_path: Optional[Path]


def _asset_owner(
    *,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> VersionOwner | None:
    return asset_owner_from_metadata(
        display_name=_compact_text(asset_name),
        asset_path=_compact_text(asset_path),
        asset_id=asset_id,
    )


def _compact_text(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stream_identity_kwargs(
    dcc: str,
    *,
    source_path: Optional[Path] = None,
    backup_path: Optional[Path] = None,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
) -> _AssetStreamIdentity:
    candidate = source_path or backup_path
    resolved_stem = stem or (candidate.stem if candidate is not None else "stream")
    resolved_ext = ext or (
        candidate.suffix.lstrip(".") if candidate is not None else "dat"
    )
    normalized_ext = (resolved_ext or "dat").lstrip(".")
    return _AssetStreamIdentity(
        stream_key=stream_key_for(dcc, resolved_stem, normalized_ext),
        stem=resolved_stem,
        ext=normalized_ext,
        stream_label=f"{resolved_stem}.{normalized_ext}",
        working_path=source_path,
    )


def build_manifest(
    *,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> dict[str, Any]:
    return _build_manifest(
        owner=_asset_owner(
            asset_name=asset_name,
            asset_path=asset_path,
            asset_id=asset_id,
        )
    )


def backup_if_changed(
    source_path: Path,
    backup_dir: Path,
    manifest_path: Path,
    *,
    dcc: str,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
    version: Optional[int] = None,
    padding: int = 3,
    ensure_exists: bool = True,
    use_hash: bool = False,
    title: Optional[str] = None,
    context: Optional[str] = None,
    note: Optional[str] = None,
    tool_version: Optional[str] = None,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
    variant: Optional[str] = None,
    publish_path: Optional[Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[BackupResult]:
    stream = _stream_identity_kwargs(
        dcc,
        source_path=source_path,
        stem=stem,
        ext=ext,
    )
    return _backup_if_changed(
        source_path,
        backup_dir,
        manifest_path,
        dcc=dcc,
        stream_key=stream.stream_key,
        stem=stream.stem,
        ext=stream.ext,
        stream_label=stream.stream_label,
        working_path=stream.working_path,
        version=version,
        padding=padding,
        ensure_exists=ensure_exists,
        use_hash=use_hash,
        title=title,
        context=context,
        note=note,
        tool_version=tool_version,
        owner=_asset_owner(
            asset_name=asset_name,
            asset_path=asset_path,
            asset_id=asset_id,
        ),
        variant=variant,
        publish_path=publish_path,
        extra=extra,
    )


def record_publish(
    manifest_path: Path,
    *,
    dcc: str,
    source_path: Optional[Path] = None,
    backup_path: Optional[Path] = None,
    version: Optional[int] = None,
    user: Optional[str] = None,
    host: Optional[str] = None,
    title: Optional[str] = None,
    context: Optional[str] = None,
    note: Optional[str] = None,
    tool_version: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> dict[str, Any]:
    stream = _stream_identity_kwargs(
        dcc,
        source_path=source_path,
        backup_path=backup_path,
    )
    return _record_publish(
        manifest_path,
        dcc=dcc,
        stream_key=stream.stream_key,
        stem=stream.stem,
        ext=stream.ext,
        stream_label=stream.stream_label,
        working_path=stream.working_path,
        source_path=source_path,
        backup_path=backup_path,
        version=version,
        user=user,
        host=host,
        title=title,
        context=context,
        note=note,
        tool_version=tool_version,
        extra=extra,
        owner=_asset_owner(
            asset_name=asset_name,
            asset_path=asset_path,
            asset_id=asset_id,
        ),
    )


__all__ = [
    "backup_file",
    "backup_if_changed",
    "build_manifest",
    "compute_signature",
    "BackupResult",
    "VersionRecord",
    "current_record",
    "get_manifest_path",
    "history_as_records",
    "list_versions",
    "load_manifest",
    "next_version",
    "record_publish",
    "save_manifest",
    "version_label",
    "versioned_filename",
]
