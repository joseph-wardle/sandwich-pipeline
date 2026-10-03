"""Houdini shot-department enumeration shared by the file manager and any
downstream tools that need to recognise department subfolders in HIP paths
(e.g. the publish, which decides what a hip publishes from its folder)."""

from __future__ import annotations

from enum import Enum


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


__all__ = ["DEPARTMENT_OPTIONS", "PUBLISHING_DEPARTMENTS", "Department"]
