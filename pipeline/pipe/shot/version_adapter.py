"""Shot-specific adapters for the shared versioning core.

Shot versioning needs explicit stream identity because a single shot root can own
multiple working-file streams across departments and DCCs. This module keeps that
translation in one place so DCC integrations stay thin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from shared.util import get_production_path

from pipe.struct.db import Shot
from pipe.versioning import (
    VersionOwner,
    VersionSnapshotMember,
    VersionStreamSpec,
    get_manifest_path,
    path_matches_stream,
    stream_dirname,
    stream_key_for,
)

DCC_HOUDINI = "houdini"
DCC_MAYA = "maya"
SHOT_VERSION_MANIFEST_FILENAME = "version_manifest.json"


def _normalized_text(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    snapshot_members: tuple[VersionSnapshotMember, ...] = (),
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
        backup_dir=root_path / ".backup" / stream_dirname(stream_key),
        dcc=resolved_dcc,
        stem=resolved_stem,
        ext=resolved_ext,
        owner=owner,
        label=_normalized_text(label) or working_path.name,
        stream_key=stream_key,
        working_path=working_path,
        snapshot_members=snapshot_members,
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


def maya_rlo_stream(
    shot: Shot,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    scene_relative_path = Path("rlo") / f"{shot.code}.mb"
    return shot_stream(
        shot,
        DCC_MAYA,
        stream_name="rlo",
        subpath="rlo",
        stem=shot.code,
        ext="mb",
        owner=owner,
        label="RLO Scene",
        snapshot_members=(
            VersionSnapshotMember(
                relative_path=scene_relative_path,
                label="RLO Scene",
                primary=True,
            ),
            VersionSnapshotMember(
                relative_path=Path("maya_root.usd"),
                label="Shot Root Layer",
            ),
            VersionSnapshotMember(
                relative_path=Path("set") / "maya_override.usd",
                label="Shot Override Layer",
            ),
        ),
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
