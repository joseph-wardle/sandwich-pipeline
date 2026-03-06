"""Shot-specific adapters for the shared versioning core.

Shot versioning needs explicit stream identity because a single shot root can own
multiple working-file streams across departments and DCCs. This module keeps that
translation in one place so DCC integrations stay thin.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from shared.util import get_production_path

from pipe.struct.db import Shot
from pipe.versioning import (
    VersionOwner,
    VersionStreamSpec,
    get_manifest_path,
    stream_key_for,
)

DCC_HOUDINI = "houdini"
DCC_MAYA = "maya"
SHOT_VERSION_MANIFEST_FILENAME = "version_manifest.json"
_STREAM_DIRNAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _normalized_text(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stream_dirname(stream_key: str) -> str:
    normalized = _STREAM_DIRNAME_RE.sub("_", stream_key).strip("._")
    return normalized or "stream"


def shot_root_path(shot: Shot) -> Path:
    return (get_production_path() / shot.shot_path).resolve()


def shot_owner_for(shot: Shot) -> VersionOwner:
    return VersionOwner(
        kind="shot",
        code=shot.code,
        display_name=shot.code,
        path=shot.shot_path,
        id=shot.id,
    )


def shot_stream(
    shot: Shot,
    dcc: str,
    *,
    stream_name: str,
    subpath: str,
    stem: str,
    ext: str,
    owner: VersionOwner | None = None,
    label: str | None = None,
) -> VersionStreamSpec:
    resolved_dcc = _normalized_text(dcc) or "unknown"
    resolved_stream_name = _normalized_text(stream_name) or stem
    resolved_subpath = _normalized_text(subpath) or ""
    resolved_stem = _normalized_text(stem) or shot.code
    resolved_ext = (_normalized_text(ext) or "").lstrip(".") or "dat"
    root_path = shot_root_path(shot)
    stream_key = stream_key_for(resolved_dcc, resolved_stream_name, resolved_ext)
    working_path = root_path / resolved_subpath / f"{resolved_stem}.{resolved_ext}"
    return VersionStreamSpec(
        root_path=root_path,
        manifest_path=get_manifest_path(
            root_path,
            filename=SHOT_VERSION_MANIFEST_FILENAME,
        ),
        backup_dir=root_path / ".backup" / _stream_dirname(stream_key),
        dcc=resolved_dcc,
        stem=resolved_stem,
        ext=resolved_ext,
        owner=owner,
        label=_normalized_text(label) or working_path.name,
        stream_key=stream_key,
        working_path=working_path,
    )


def maya_anim_stream(
    shot: Shot,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    return shot_stream(
        shot,
        DCC_MAYA,
        stream_name="anim",
        subpath="anim",
        stem=shot.code,
        ext="mb",
        owner=owner,
        label="Animation Scene",
    )


def houdini_department_stream(
    shot: Shot,
    department: str,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    resolved_department = _normalized_text(department) or "unknown"
    return shot_stream(
        shot,
        DCC_HOUDINI,
        stream_name=resolved_department,
        subpath=resolved_department,
        stem=resolved_department,
        ext="hipnc",
        owner=owner,
        label=f"{resolved_department.upper()} Scene",
    )


def path_matches_stream(path: Path, stream: VersionStreamSpec) -> bool:
    resolved_path = Path(path).expanduser().resolve()

    working_path = stream.working_path
    if working_path is not None:
        resolved_working_path = Path(working_path).expanduser().resolve()
        if resolved_path == resolved_working_path:
            return True

    resolved_backup_dir = Path(stream.backup_dir).expanduser().resolve()
    if resolved_path.parent != resolved_backup_dir:
        return False
    return resolved_path.suffix.lower() == f".{stream.ext.lower()}"


__all__ = [
    "DCC_HOUDINI",
    "DCC_MAYA",
    "SHOT_VERSION_MANIFEST_FILENAME",
    "houdini_department_stream",
    "maya_anim_stream",
    "path_matches_stream",
    "shot_owner_for",
    "shot_root_path",
    "shot_stream",
]
