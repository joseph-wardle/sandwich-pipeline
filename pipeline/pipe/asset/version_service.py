"""Asset compatibility wrappers around the shared version service."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipe.versioning import (
    VersionRecord,
)
from pipe.versioning import (
    list_version_records as _list_version_records,
)
from pipe.versioning import (
    promote_version as _promote_version,
)
from pipe.versioning import (
    save_version as _save_version,
)

from .paths import AssetPaths
from .version_adapter import asset_stream


def _asset_stream(
    asset_paths: AssetPaths,
    dcc: str,
    *,
    stem: str,
    ext: str,
):
    return asset_stream(asset_paths, dcc, stem=stem, ext=ext)


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
    return _save_version(
        source_path,
        _asset_stream(asset_paths, dcc, stem=stem, ext=ext),
        title=title,
        note=note,
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
    return _promote_version(
        record,
        _asset_stream(asset_paths, dcc, stem=stem, ext=ext),
        title=title,
        note=note,
    )


def list_version_records(
    asset_paths: AssetPaths,
    dcc: str,
    stem: str,
    ext: str,
) -> list[VersionRecord]:
    return _list_version_records(_asset_stream(asset_paths, dcc, stem=stem, ext=ext))


__all__ = [
    "list_version_records",
    "promote_version",
    "save_version",
]
