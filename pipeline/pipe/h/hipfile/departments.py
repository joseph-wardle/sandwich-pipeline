"""Houdini shot-department enumeration shared by the file manager and any
downstream tools that need to recognise department subfolders in HIP paths
(e.g. the playblast HUD's HIP-version detection)."""

from __future__ import annotations

from enum import Enum


class Department(str, Enum):
    """Departments that own a Houdini shot save-stream subfolder. The
    string value is the lowercased folder name as it appears on disk."""

    CFX = "cfx"
    FX = "fx"
    LIGHTING = "lighting"
    ENVFX = "envfx"
    FLO = "flo"
    RENDER = "render"


DEPARTMENT_OPTIONS: tuple[str, ...] = tuple(member.value for member in Department)


__all__ = ["DEPARTMENT_OPTIONS", "Department"]
