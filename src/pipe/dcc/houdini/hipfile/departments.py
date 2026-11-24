"""Houdini shot-department enumeration shared by the file manager and any
downstream tools that need to recognise department subfolders in HIP paths
(e.g. the publish, which decides what a hip publishes from its folder)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pipe.core.util.paths import get_cache_path, get_production_path

SHOTS_DIRNAME = "shot"


class Department(str, Enum):
    """Departments that own a subfolder of Houdini hips in a shot. The
    string value is the lowercased folder name as it appears on disk."""

    CFX = "cfx"
    FX = "fx"
    LIGHTING = "lighting"
    ENVFX = "envfx"
    FLO = "flo"
    RENDER = "render"


DEPARTMENT_OPTIONS: tuple[str, ...] = tuple(member.value for member in Department)
# A render hip reads every department's publish and publishes nothing itself.
PUBLISHING_DEPARTMENTS: tuple[str, ...] = tuple(
    member.value for member in Department if member is not Department.RENDER
)


def shot_folder(hip: Path) -> Path | None:
    """The production folder of the shot whose department folder is `hip`, or None"""
    for root in (get_production_path(), get_cache_path()):
        if not hip.is_relative_to(root):
            continue
        parts = hip.relative_to(root).parts
        if (
            len(parts) == 3
            and parts[0] == SHOTS_DIRNAME
            and parts[2] in DEPARTMENT_OPTIONS
        ):
            return get_production_path() / parts[0] / parts[1]
    return None


__all__ = [
    "DEPARTMENT_OPTIONS",
    "PUBLISHING_DEPARTMENTS",
    "SHOTS_DIRNAME",
    "Department",
    "shot_folder",
]
