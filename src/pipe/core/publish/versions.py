"""A publish folder's immutable versions, and the current layer that points at one.

publish/
├── <name>.usda         current: sublayers ./v002/<name>.usd
├── v001/<name>.usd
├── v002/<name>.usd
└── .v003.tmp/          a version being written

Every function takes the current layer's path, which names the folder and the
version layers in it.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from pxr import Sdf

_VERSION = re.compile(r"v([0-9]{3,})")
_STAGE_INFO_KEYS = ("metersPerUnit", "upAxis")


def version_layer_path(current: Path, version: int) -> Path:
    return current.parent / _version_dirname(version) / f"{current.stem}.usd"


def staging_layer_path(current: Path, version: int) -> Path:
    """Where a version is written before it is committed.

    A sibling of the version directory, so the relative asset paths written into
    the layer stay valid when it is renamed.
    """
    staging = f".{_version_dirname(version)}.tmp"
    return current.parent / staging / f"{current.stem}.usd"


def create_staging(current: Path, version: int) -> Path:
    """Create the empty folder a version is written into; return its layer path.

    Raises:
        FileExistsError: Another publish of `version` is running, or one stopped
            partway and left its folder behind.
    """
    staging = staging_layer_path(current, version).parent
    staging.parent.mkdir(exist_ok=True)
    # Never reused: whatever is already in it would be committed with this version.
    staging.mkdir()
    return staging_layer_path(current, version)


def next_version(current: Path) -> int:
    if not current.parent.is_dir():
        return 1
    versions = [
        int(match.group(1))
        for entry in current.parent.iterdir()
        if entry.is_dir() and (match := _VERSION.fullmatch(entry.name))
    ]
    return max(versions, default=0) + 1


def discard_staged(current: Path, version: int) -> None:
    shutil.rmtree(staging_layer_path(current, version).parent)


def commit_version(current: Path, version: int) -> Path:
    """Rename the staged version into place. It is read once it is made current."""
    staging = staging_layer_path(current, version).parent
    staging.rename(version_layer_path(current, version).parent)
    return version_layer_path(current, version)


def make_current(current: Path, version: int) -> None:
    """Point the current layer at `version`. Also how a publish is rolled back.

    Raises:
        FileNotFoundError: `version` was never published.
    """
    version_path = version_layer_path(current, version)
    version_layer = Sdf.Layer.FindOrOpen(str(version_path))
    if version_layer is None:
        raise FileNotFoundError(f"There is no version {version}: {version_path}")

    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.subLayerPaths.append(f"./{_version_dirname(version)}/{version_path.name}")
    layer.defaultPrim = version_layer.defaultPrim
    for key in _STAGE_INFO_KEYS:
        if version_layer.pseudoRoot.HasInfo(key):
            layer.pseudoRoot.SetInfo(key, version_layer.pseudoRoot.GetInfo(key))

    # Written beside the current layer and swapped in, so a reader opening it
    # mid-publish gets either the old current layer or the new one, never half.
    temp = current.with_name(f".{current.stem}.tmp{current.suffix}")
    layer.Export(str(temp))
    os.replace(temp, current)


def _version_dirname(version: int) -> str:
    return f"v{version:03d}"
