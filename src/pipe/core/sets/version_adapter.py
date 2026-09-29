"""Set adapters for the shared versioning core: the set hip's save history."""

from __future__ import annotations

from pathlib import Path

from pipe.core.shotgrid import Set
from pipe.core.versioning import (
    DCC_HOUDINI,
    VERSION_MANIFEST_FILENAME,
    VersionOwner,
    VersionStreamSpec,
    get_manifest_path,
    stream_dirname,
    stream_key_for,
)

from .publish import set_dir

SET_STREAM_NAME = "set"
_HIP_EXT = "hipnc"


def set_owner_for(set: Set) -> VersionOwner:
    return VersionOwner(
        kind="set",
        code=set.name,
        display_name=set.display_name,
        path=set.path,
        id=set.id,
    )


def houdini_set_stream(set: Set) -> VersionStreamSpec:
    root_path = set_dir(set.name)
    stream_key = stream_key_for(DCC_HOUDINI, SET_STREAM_NAME, _HIP_EXT)
    return VersionStreamSpec(
        root_path=root_path,
        manifest_path=get_manifest_path(root_path, filename=VERSION_MANIFEST_FILENAME),
        backup_dir=root_path / ".backup" / stream_dirname(stream_key),
        dcc=DCC_HOUDINI,
        stem=set.name,
        ext=_HIP_EXT,
        owner=set_owner_for(set),
        label="Set Scene",
        stream_key=stream_key,
        working_path=hip_path(set),
    )


def hip_path(set: Set) -> Path:
    return set_dir(set.name) / f"{set.name}.{_HIP_EXT}"
