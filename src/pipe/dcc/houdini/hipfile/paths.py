"""The open HIP's path. Qt-free so callers can use it without pulling in the
Qt-laden file managers."""

from __future__ import annotations

from pathlib import Path

import hou


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


__all__ = ["current_hip_path"]
