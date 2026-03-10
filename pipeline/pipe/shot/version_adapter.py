"""Shot-specific adapters for the shared versioning core.

Shot versioning needs explicit stream identity because a single shot root can own
multiple working-file streams across departments and DCCs. This module keeps that
translation in one place so DCC integrations stay thin.
"""

from __future__ import annotations

from pathlib import Path

from shared.util import get_production_path

from pipe.struct.db import Shot
from pipe.versioning import (
    VersionOwner,
    VersionSnapshotMember,
    VersionStreamSpec,
    stream_dirname,
    stream_key_for,
)
from pipe.versioning.model import DCC_HOUDINI, DCC_MAYA, _normalize_text
from pipe.versioning.store import VERSION_MANIFEST_FILENAME, get_manifest_path


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
    resolved_dcc = _normalize_text(dcc) or "unknown"
    resolved_stream_name = _normalize_text(stream_name) or stem
    resolved_subpath = _normalize_text(subpath) or ""
    resolved_stem = _normalize_text(stem) or shot.code
    resolved_ext = (_normalize_text(ext) or "").lstrip(".") or "dat"
    root_path = shot_root_path(shot)
    stream_key = stream_key_for(resolved_dcc, resolved_stream_name, resolved_ext)
    working_path = root_path / resolved_subpath / f"{resolved_stem}.{resolved_ext}"
    return VersionStreamSpec(
        root_path=root_path,
        manifest_path=get_manifest_path(
            root_path,
            filename=VERSION_MANIFEST_FILENAME,
        ),
        backup_dir=root_path / ".backup" / stream_dirname(stream_key),
        dcc=resolved_dcc,
        stem=resolved_stem,
        ext=resolved_ext,
        owner=owner,
        label=_normalize_text(label) or working_path.name,
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
    resolved_department = _normalize_text(department) or "unknown"
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
    "houdini_department_stream",
    "maya_anim_stream",
    "maya_rlo_stream",
    "shot_owner_for",
    "shot_root_path",
    "shot_stream",
]
