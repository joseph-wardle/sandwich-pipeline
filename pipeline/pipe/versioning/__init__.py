"""Shared versioning core for working-file history workflows."""

from .model import BackupResult, VersionOwner, VersionRecord, VersionStreamSpec
from .service import list_version_records, promote_version, save_version
from .store import (
    DEFAULT_MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    backup_file,
    backup_if_changed,
    build_manifest,
    compute_signature,
    get_manifest_path,
    history_as_records,
    list_versions,
    load_manifest,
    next_version,
    record_publish,
    save_manifest,
    version_label,
    versioned_filename,
)

__all__ = [
    "BackupResult",
    "DEFAULT_MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "VersionOwner",
    "VersionRecord",
    "VersionStreamSpec",
    "backup_file",
    "backup_if_changed",
    "build_manifest",
    "compute_signature",
    "get_manifest_path",
    "history_as_records",
    "list_version_records",
    "list_versions",
    "load_manifest",
    "next_version",
    "promote_version",
    "record_publish",
    "save_version",
    "save_manifest",
    "version_label",
    "versioned_filename",
]
