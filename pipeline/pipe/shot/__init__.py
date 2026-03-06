"""Shot-specific adapters for the shared versioning core."""

from .version_adapter import (
    DCC_HOUDINI,
    DCC_MAYA,
    SHOT_VERSION_MANIFEST_FILENAME,
    houdini_department_stream,
    maya_anim_stream,
    maya_rlo_stream,
    path_matches_stream,
    shot_owner_for,
    shot_root_path,
    shot_stream,
)

__all__ = [
    "DCC_HOUDINI",
    "DCC_MAYA",
    "SHOT_VERSION_MANIFEST_FILENAME",
    "houdini_department_stream",
    "maya_anim_stream",
    "maya_rlo_stream",
    "path_matches_stream",
    "shot_owner_for",
    "shot_root_path",
    "shot_stream",
]
