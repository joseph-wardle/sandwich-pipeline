"""Core versioning dataclasses and identifiers shared across pipeline domains.

The model is intentionally small:
1. ``VersionOwner`` identifies the root entity when metadata should be recorded.
2. ``VersionStreamSpec`` identifies one versioned working-file stream under a root.
3. ``VersionSnapshotMember`` optionally describes extra files captured with a
   compound snapshot.
4. ``VersionRecord`` and ``BackupResult`` describe persisted history entries.

Naming helpers (``stream_filename``, ``stream_key_for``, ``stream_dirname``) live
here because they operate on plain strings and carry no dependencies beyond the
standard library.

DCC identifiers
---------------
``DCC_MAYA``, ``DCC_HOUDINI``, and ``DCC_SUBSTANCE`` are the canonical string
values used in ``VersionStreamSpec.dcc``.  They live here so every domain adapter
can import a single authoritative definition instead of each spelling its own
string literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Canonical DCC identifier strings used in VersionStreamSpec.dcc.
DCC_MAYA = "maya"
DCC_HOUDINI = "houdini"
DCC_SUBSTANCE = "substance_painter"

_STREAM_DIRNAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _normalize_text(value: object | None) -> Optional[str]:
    """Strip *value* to a non-empty string, or return ``None``.

    Shared by all domain adapters when normalising ``VersionOwner`` fields from
    DCC metadata that may be ``None``, empty, or whitespace-only.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def stream_filename(stem: str, ext: str) -> str:
    """Return the canonical filename for a stream (e.g. ``"model.mb"``)."""
    normalized_stem = str(stem).strip()
    normalized_ext = str(ext).strip().lstrip(".")
    return f"{normalized_stem}.{normalized_ext}"


def stream_key_for(dcc: str, stem: str, ext: str) -> str:
    """Return the stable manifest key for a versioned stream.

    Keys are scoped by DCC so the same filename used in two different DCCs
    produces distinct history buckets (e.g. ``"maya:model.mb"``).
    """
    normalized_dcc = str(dcc).strip()
    return f"{normalized_dcc}:{stream_filename(stem, ext)}"


def stream_dirname(stream_key: str) -> str:
    """Return a filesystem-safe directory name derived from a stream key.

    Used to scope backup directories per stream so multiple streams under
    the same root never collide (e.g. ``"maya_model.mb"``).
    """
    normalized = _STREAM_DIRNAME_UNSAFE.sub("_", str(stream_key).strip()).strip("._")
    return normalized or "stream"


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
    "DCC_HOUDINI",
    "DCC_MAYA",
    "DCC_SUBSTANCE",
    "VersionOwner",
    "VersionRecord",
    "VersionSnapshotMember",
    "VersionStreamSpec",
    "stream_dirname",
    "stream_filename",
    "stream_key_for",
]
