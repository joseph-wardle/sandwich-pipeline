"""Asset compatibility wrappers around the shared versioning core.

The implementation now lives in :mod:`pipe.versioning`. This module keeps the
asset-facing API stable while the wider pipeline migrates to the shared package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pipe.versioning import (
    BackupResult,
    VersionOwner,
    VersionRecord,
    backup_file,
    compute_signature,
    get_manifest_path,
    history_as_records,
    list_versions,
    load_manifest,
    next_version,
    save_manifest,
    version_label,
    versioned_filename,
)
from pipe.versioning import (
    build_manifest as _build_manifest,
)
from pipe.versioning import (
    record_publish as _record_publish,
)
from pipe.versioning.store import backup_if_changed as _backup_if_changed


def _asset_owner(
    *,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> VersionOwner | None:
    if asset_name is None and asset_path is None and asset_id is None:
        return None
    normalized_name = _compact_text(asset_name)
    fallback_code = normalized_name or _compact_text(asset_path) or "asset"
    return VersionOwner(
        kind="asset",
        code=fallback_code,
        display_name=normalized_name,
        path=_compact_text(asset_path),
        id=asset_id,
    )


def _compact_text(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    return _backup_if_changed(
        source_path,
        backup_dir,
        manifest_path,
        dcc=dcc,
        stem=stem,
        ext=ext,
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
    return _record_publish(
        manifest_path,
        dcc=dcc,
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
