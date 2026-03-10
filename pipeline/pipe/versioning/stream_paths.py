"""Helpers for stream-scoped backup path handling.

These helpers keep path conventions in one place so domain adapters
(asset/shot/environment) can remain small and focused on stream identity.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import VersionStreamSpec

_STREAM_DIRNAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_BUNDLE_DIRNAME_RE = re.compile(r"^v\d+$")


def stream_dirname(stream_key: str) -> str:
    """Return a filesystem-safe directory name for a stream key."""
    normalized = _STREAM_DIRNAME_RE.sub("_", str(stream_key).strip()).strip("._")
    return normalized or "stream"


def path_matches_stream(path: Path, stream: VersionStreamSpec) -> bool:
    """Return ``True`` when a file path belongs to a stream's working or backup set."""
    resolved_path = Path(path).expanduser().resolve()

    for member in stream.snapshot_members:
        if resolved_path == (Path(stream.root_path) / member.relative_path).resolve():
            return True

    working_path = stream.working_path
    if working_path is not None:
        resolved_working_path = Path(working_path).expanduser().resolve()
        if resolved_path == resolved_working_path:
            return True

    resolved_backup_dir = Path(stream.backup_dir).expanduser().resolve()
    try:
        relative_to_backup = resolved_path.relative_to(resolved_backup_dir)
    except Exception:
        return False

    if stream.snapshot_members:
        if len(relative_to_backup.parts) < 2:
            return False
        bundle_name = relative_to_backup.parts[0]
        if not _BUNDLE_DIRNAME_RE.match(bundle_name):
            return False
        member_path = Path(*relative_to_backup.parts[1:])
        return member_path in {
            snapshot_member.relative_path for snapshot_member in stream.snapshot_members
        }

    return (
        resolved_path.parent == resolved_backup_dir
        and resolved_path.suffix.lower() == f".{stream.ext.lower()}"
    )


__all__ = ["path_matches_stream", "stream_dirname"]
