"""Where a set's published versions live on disk, and how one becomes current."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from pxr import Sdf, Usd, UsdGeom

from pipe.core.util.paths import get_production_path

SETS_DIRNAME = "set"
PUBLISH_DIRNAME = "publish"
SOURCE_KEY = "source"

_NAME = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
_VERSION = re.compile(r"v([0-9]{3,})")
_STAGE_INFO_KEYS = ("metersPerUnit", "upAxis")
# Whoever publishes next must be able to add a version and replace the current
# layer, whatever the publishing session's umask and primary group.
_SHARED_DIR_MODE = 0o770
_SHARED_FILE_MODE = 0o660


def valid_set_name(name: str) -> bool:
    """Whether `name` can be both a set's folder and its USD root prim."""
    return bool(_NAME.fullmatch(name))


def set_dir(name: str) -> Path:
    return get_production_path() / SETS_DIRNAME / name


def current_layer_path(name: str) -> Path:
    return set_dir(name) / PUBLISH_DIRNAME / f"{name}.usda"


def version_layer_path(name: str, version: int) -> Path:
    return set_dir(name) / PUBLISH_DIRNAME / _version_dirname(version) / f"{name}.usd"


def staging_layer_path(name: str, version: int) -> Path:
    """Where a version is written before it is committed.

    A sibling of the version directory, so the relative asset paths the USD ROP
    writes stay valid when it is renamed.
    """
    staging = f".{_version_dirname(version)}.tmp"
    return set_dir(name) / PUBLISH_DIRNAME / staging / f"{name}.usd"


def create_staging(name: str, version: int) -> Path:
    """Create the empty folder a version is written into; return its layer path."""
    group = _group(name)
    staging = staging_layer_path(name, version).parent
    for folder in (staging.parent, staging):
        if not folder.exists():
            folder.mkdir()
            _share(folder, group)
    return staging_layer_path(name, version)


def next_version(name: str) -> int:
    publish_dir = set_dir(name) / PUBLISH_DIRNAME
    if not publish_dir.is_dir():
        return 1
    versions = [
        int(match.group(1))
        for entry in publish_dir.iterdir()
        if entry.is_dir() and (match := _VERSION.fullmatch(entry.name))
    ]
    return max(versions, default=0) + 1


def prepare_layer(layer_path: Path, name: str, source: Path) -> list[str]:
    """Author the set contract onto a freshly written layer and save it.

    Returns the root prims other than `/<name>`, which shots will never see.

    Raises:
        ValueError: The layer has no `/<name>` prim.
    """
    stage = Usd.Stage.Open(str(layer_path), load=Usd.Stage.LoadNone)
    root = stage.GetPrimAtPath(f"/{name}")
    if not root.IsValid():
        raise ValueError(f"{layer_path} has no /{name} prim.")

    stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.LinearUnits.meters)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    layer = stage.GetRootLayer()
    layer.customLayerData = {**layer.customLayerData, SOURCE_KEY: str(source)}
    layer.Save()

    return [
        prim.GetName()
        for prim in stage.GetPseudoRoot().GetAllChildren()
        if prim != root
    ]


def discard_staged(name: str, version: int) -> None:
    shutil.rmtree(staging_layer_path(name, version).parent)


def commit_version(name: str, version: int) -> Path:
    """Rename the staged version into place. Shots read it once it is made current."""
    staging = staging_layer_path(name, version).parent
    group = _group(name)
    for path in (staging, *staging.rglob("*")):
        _share(path, group)
    staging.rename(version_layer_path(name, version).parent)
    return version_layer_path(name, version)


def make_current(name: str, version: int) -> None:
    """Point the set's current layer at `version`. Also how a TD rolls back.

    Raises:
        FileNotFoundError: `version` was never published.
    """
    version_path = version_layer_path(name, version)
    version_layer = Sdf.Layer.FindOrOpen(str(version_path))
    if version_layer is None:
        raise FileNotFoundError(f"Set {name} has no version {version}: {version_path}")

    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.subLayerPaths.append(f"./{_version_dirname(version)}/{name}.usd")
    layer.defaultPrim = version_layer.defaultPrim
    for key in _STAGE_INFO_KEYS:
        if version_layer.pseudoRoot.HasInfo(key):
            layer.pseudoRoot.SetInfo(key, version_layer.pseudoRoot.GetInfo(key))

    # Written beside the current layer and swapped in, so a shot opening the set
    # mid-publish reads either the old current layer or the new one, never half.
    current = current_layer_path(name)
    temp = current.with_name(f".{name}.tmp.usda")
    layer.Export(str(temp))
    _share(temp, _group(name))
    os.replace(temp, current)


def _group(name: str) -> int:
    return set_dir(name).stat().st_gid


def _share(path: Path, group: int) -> None:
    os.chown(path, -1, group)
    path.chmod(_SHARED_DIR_MODE if path.is_dir() else _SHARED_FILE_MODE)


def _version_dirname(version: int) -> str:
    return f"v{version:03d}"
