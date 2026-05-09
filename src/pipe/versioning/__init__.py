"""Shared versioning core for working-file history workflows."""

from .model import (
    BackupResult,
    DCC_HOUDINI,
    DCC_MAYA,
    DCC_SUBSTANCE,
    VersionOwner,
    VersionRecord,
    VersionSnapshotMember,
    VersionStreamSpec,
    stream_dirname,
    stream_filename,
    stream_key_for,
)
from .service import (
    list_version_records,
    path_matches_stream,
    promote_version,
    save_version,
)
from .store import version_label

__all__ = [
    "BackupResult",
    "DCC_HOUDINI",
    "DCC_MAYA",
    "DCC_SUBSTANCE",
    "VersionOwner",
    "VersionRecord",
    "VersionSnapshotMember",
    "VersionStreamSpec",
    "list_version_records",
    "path_matches_stream",
    "promote_version",
    "save_version",
    "stream_dirname",
    "stream_filename",
    "stream_key_for",
    "version_label",
]
