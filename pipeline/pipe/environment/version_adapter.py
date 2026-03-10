"""Environment (set) adapters for the shared versioning core."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipe.struct.db import Environment
from pipe.versioning import (
    VersionOwner,
    VersionStreamSpec,
    get_manifest_path,
    path_matches_stream,
    stream_dirname,
    stream_key_for,
)
from shared.util import get_production_path

DCC_HOUDINI = "houdini"
ENVIRONMENT_VERSION_MANIFEST_FILENAME = "version_manifest.json"
SET_STREAM_NAME = "set"


def _normalized_text(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def environment_root_path(environment: Environment) -> Path:
    environment_path = _normalized_text(environment.path)
    if environment_path is None:
        raise ValueError(
            f"Environment {environment.code or environment.id} has no path in ShotGrid."
        )
    return (get_production_path() / environment_path).resolve()


def environment_owner_for(environment: Environment) -> VersionOwner:
    display_name = _normalized_text(environment.display_name)
    normalized_name = _normalized_text(environment.name)
    environment_path = _normalized_text(environment.path)
    return VersionOwner(
        kind="environment",
        code=display_name or normalized_name or environment_path or "environment",
        display_name=display_name or normalized_name,
        path=environment_path,
        id=environment.id,
    )


def houdini_set_stream(
    environment: Environment,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    root_path = environment_root_path(environment)
    stem = _normalized_text(environment.name) or "set"
    ext = "hipnc"
    stream_key = stream_key_for(DCC_HOUDINI, SET_STREAM_NAME, ext)
    working_path = root_path / f"{stem}.{ext}"
    return VersionStreamSpec(
        root_path=root_path,
        manifest_path=get_manifest_path(
            root_path,
            filename=ENVIRONMENT_VERSION_MANIFEST_FILENAME,
        ),
        backup_dir=root_path / ".backup" / stream_dirname(stream_key),
        dcc=DCC_HOUDINI,
        stem=stem,
        ext=ext,
        owner=owner,
        label="Set Scene",
        stream_key=stream_key,
        working_path=working_path,
    )


__all__ = [
    "DCC_HOUDINI",
    "ENVIRONMENT_VERSION_MANIFEST_FILENAME",
    "SET_STREAM_NAME",
    "environment_owner_for",
    "environment_root_path",
    "houdini_set_stream",
    "path_matches_stream",
]
