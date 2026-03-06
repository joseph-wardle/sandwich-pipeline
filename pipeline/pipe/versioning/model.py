"""Core versioning dataclasses shared across pipeline domains.

The model is intentionally small:
1. ``VersionOwner`` identifies the root entity when metadata should be recorded.
2. ``VersionStreamSpec`` identifies one versioned working-file stream under a root.
3. ``VersionRecord`` and ``BackupResult`` describe persisted history entries.

This phase keeps the asset manifest storage format unchanged while moving the core
types out of ``pipe.asset`` so future shot and tool adapters can share them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class BackupResult:
    changed: bool
    signature: dict[str, Any]
    backup_path: Optional[Path]
    version: Optional[int]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class VersionRecord:
    version: Optional[int]
    title: Optional[str]
    note: Optional[str]
    context: Optional[str]
    user: Optional[str]
    timestamp: Optional[str]
    backup_path: Optional[Path]
    source_file: Optional[str]


@dataclass(frozen=True)
class VersionOwner:
    """Optional identity metadata for the root that owns a version stream."""

    kind: str
    code: str
    display_name: Optional[str] = None
    path: Optional[str] = None
    id: Optional[int] = None


@dataclass(frozen=True)
class VersionStreamSpec:
    """Describe one versioned working-file stream under a root path."""

    root_path: Path
    manifest_path: Path
    backup_dir: Path
    dcc: str
    stem: str
    ext: str
    owner: Optional[VersionOwner] = None
    label: Optional[str] = None
    stream_key: Optional[str] = None
    working_path: Optional[Path] = None


__all__ = [
    "BackupResult",
    "VersionOwner",
    "VersionRecord",
    "VersionStreamSpec",
]
