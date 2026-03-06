"""Core versioning dataclasses and identifiers shared across pipeline domains.

The model is intentionally small:
1. ``VersionOwner`` identifies the root entity when metadata should be recorded.
2. ``VersionStreamSpec`` identifies one versioned working-file stream under a root.
3. ``VersionSnapshotMember`` optionally describes extra files captured with a
   compound snapshot.
4. ``VersionRecord`` and ``BackupResult`` describe persisted history entries.

The shared store now persists stream-keyed manifests while remaining able to read
legacy asset manifests keyed only by DCC.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def stream_filename(stem: str, ext: str) -> str:
    normalized_stem = str(stem).strip()
    normalized_ext = str(ext).strip().lstrip(".")
    return f"{normalized_stem}.{normalized_ext}"


def stream_key_for(dcc: str, stem: str, ext: str) -> str:
    """Return the stable manifest key for a versioned stream."""
    normalized_dcc = str(dcc).strip()
    return f"{normalized_dcc}:{stream_filename(stem, ext)}"


@dataclass(frozen=True)
class BackupResult:
    changed: bool
    signature: dict[str, Any]
    backup_path: Optional[Path]
    version: Optional[int]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class VersionSnapshotMember:
    """Describe one file included in a compound version snapshot."""

    relative_path: Path
    label: Optional[str] = None
    primary: bool = False
    required: bool = True


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
    backup_root: Optional[Path] = None
    backup_members: tuple[str, ...] = ()


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
    """Describe one versioned working-file stream under a root path.

    ``snapshot_members`` is empty for single-file streams. When populated, the
    stream is treated as a compound snapshot whose version storage preserves the
    listed root-relative files together.
    """

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
    snapshot_members: tuple[VersionSnapshotMember, ...] = ()


__all__ = [
    "BackupResult",
    "VersionOwner",
    "VersionRecord",
    "VersionSnapshotMember",
    "VersionStreamSpec",
    "stream_filename",
    "stream_key_for",
]
