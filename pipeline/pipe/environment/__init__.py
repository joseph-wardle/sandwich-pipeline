"""Environment (set) adapters for the shared versioning core."""

from .version_adapter import (
    DCC_HOUDINI,
    ENVIRONMENT_VERSION_MANIFEST_FILENAME,
    SET_STREAM_NAME,
    environment_owner_for,
    environment_root_path,
    houdini_set_stream,
    path_matches_stream,
)

__all__ = [
    "DCC_HOUDINI",
    "ENVIRONMENT_VERSION_MANIFEST_FILENAME",
    "SET_STREAM_NAME",
    "environment_owner_for",
    "environment_root_path",
    "houdini_set_stream",
    "path_matches_stream",
]
