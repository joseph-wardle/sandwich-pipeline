"""Shot-specific adapters for the shared versioning core."""

from .version_adapter import (
    houdini_department_stream,
    maya_anim_stream,
    maya_rlo_stream,
    shot_owner_for,
    shot_root_path,
    shot_stream,
)

__all__ = [
    "houdini_department_stream",
    "maya_anim_stream",
    "maya_rlo_stream",
    "shot_owner_for",
    "shot_root_path",
    "shot_stream",
]
