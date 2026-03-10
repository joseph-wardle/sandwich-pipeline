"""Environment (set) adapters for the shared versioning core."""

from __future__ import annotations

from pathlib import Path

from pipe.struct.db import Environment
from pipe.versioning import (
    VersionOwner,
    VersionStreamSpec,
    stream_dirname,
    stream_key_for,
)
from pipe.versioning.model import DCC_HOUDINI, _normalize_text
from pipe.versioning.store import VERSION_MANIFEST_FILENAME, get_manifest_path
from shared.util import get_production_path

SET_STREAM_NAME = "set"


def environment_root_path(environment: Environment) -> Path:
    environment_path = _normalize_text(environment.path)
    if environment_path is None:
        raise ValueError(
            f"Environment {environment.code or environment.id} has no path in ShotGrid."
        )
    return (get_production_path() / environment_path).resolve()


def environment_owner_for(environment: Environment) -> VersionOwner:
    display_name = _normalize_text(environment.display_name)
    normalized_name = _normalize_text(environment.name)
    environment_path = _normalize_text(environment.path)
    return VersionOwner(
        kind="environment",
        code=display_name or normalized_name or environment_path or "environment",
        display_name=display_name or normalized_name,
        path=environment_path,
        id=environment.id,
    )


def environment_stream(
    environment: Environment,
    dcc: str,
    *,
    stream_name: str,
    stem: str | None = None,
    ext: str,
    owner: VersionOwner | None = None,
    label: str | None = None,
) -> VersionStreamSpec:
    root_path = environment_root_path(environment)
    resolved_dcc = _normalize_text(dcc) or "unknown"
    resolved_stream_name = _normalize_text(stream_name) or SET_STREAM_NAME
    resolved_stem = (
        _normalize_text(stem) or _normalize_text(environment.name) or SET_STREAM_NAME
    )
    resolved_ext = (_normalize_text(ext) or "").lstrip(".") or "dat"
    stream_key = stream_key_for(resolved_dcc, resolved_stream_name, resolved_ext)
    working_path = root_path / f"{resolved_stem}.{resolved_ext}"
    return VersionStreamSpec(
        root_path=root_path,
        manifest_path=get_manifest_path(root_path, filename=VERSION_MANIFEST_FILENAME),
        backup_dir=root_path / ".backup" / stream_dirname(stream_key),
        dcc=resolved_dcc,
        stem=resolved_stem,
        ext=resolved_ext,
        owner=owner,
        label=_normalize_text(label) or working_path.name,
        stream_key=stream_key,
        working_path=working_path,
    )


def houdini_set_stream(
    environment: Environment,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    return environment_stream(
        environment,
        DCC_HOUDINI,
        stream_name=SET_STREAM_NAME,
        ext="hipnc",
        owner=owner,
        label="Set Scene",
    )


__all__ = [
    "SET_STREAM_NAME",
    "environment_owner_for",
    "environment_root_path",
    "environment_stream",
    "houdini_set_stream",
]
