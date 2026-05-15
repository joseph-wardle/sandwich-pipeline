"""HIP path + department resolvers. Qt-free so callers can use them without
pulling in the Qt-laden file managers."""

from __future__ import annotations

from pathlib import Path

import hou

from dcc.houdini.hipfile.departments import DEPARTMENT_OPTIONS


def current_hip_path() -> Path | None:
    """Resolved absolute path of the current HIP file, or `None` if unsaved."""
    hip_raw = (hou.hipFile.path() or "").strip()
    if not hip_raw:
        return None
    hip_path = Path(hou.expandString(hip_raw)).expanduser()
    if not hip_path.is_absolute():
        hip_path = (Path(hou.hscriptStringExpression("$HIP")) / hip_path).resolve()
    else:
        hip_path = hip_path.resolve()
    return hip_path


def department_from_hip_path(hip_path: Path) -> str | None:
    """Match either `<department>/...hip*` or `<department>.v###.hipnc`."""
    parent_name = hip_path.parent.name.strip().lower()
    if parent_name in DEPARTMENT_OPTIONS:
        return parent_name

    if hip_path.suffix.lower() != ".hipnc":
        return None

    stem = hip_path.stem.strip().lower()
    if ".v" in stem:
        stem = stem.rsplit(".v", 1)[0]
    if stem in DEPARTMENT_OPTIONS:
        return stem
    return None


__all__ = ["current_hip_path", "department_from_hip_path"]
