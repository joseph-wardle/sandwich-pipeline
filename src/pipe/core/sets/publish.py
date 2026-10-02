"""A set's current layer on disk, what its next publish writes and what it must hold."""

from __future__ import annotations

import re
from pathlib import Path

from pxr import Usd, UsdGeom

from pipe.core.publish import PUBLISH_DIRNAME, Target, next_version
from pipe.core.shotgrid import Set
from pipe.core.util.paths import get_production_path

SETS_DIRNAME = "set"
SOURCE_KEY = "source"
# What every set version's ShotGrid PublishedFile is named.
PUBLISHED_FILE_NAME = "set"

_NAME = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")


def valid_set_name(name: str) -> bool:
    """Whether `name` can be both a set's folder and its USD root prim."""
    return bool(_NAME.fullmatch(name))


def set_dir(name: str) -> Path:
    return get_production_path() / SETS_DIRNAME / name


def current_layer_path(name: str) -> Path:
    """The layer shots read. `pipe.core.publish` versions the folder it sits in."""
    return set_dir(name) / PUBLISH_DIRNAME / f"{name}.usda"


def set_target(set: Set) -> Target:
    """The next version of a set's layer."""
    current = current_layer_path(set.name)
    version = next_version(current)
    return Target(
        entity=set,
        name=set.display_name,
        current=current,
        version=version,
        file_name=PUBLISHED_FILE_NAME,
        file_code=f"{set.name}_v{version:03d}",
        department=None,
    )


def prepare_layer(layer_path: Path, name: str, source: Path) -> list[str]:
    """Author the set contract onto a freshly written layer and save it.

    Returns the root prims other than `/<name>`, which shots will never see.

    Raises:
        ValueError: The layer has no `/<name>` prim.
    """
    stage = Usd.Stage.Open(str(layer_path), load=Usd.Stage.LoadNone)
    root = stage.GetPrimAtPath(f"/{name}")
    if not root.IsValid():
        # Closed first: the caller deletes the layer, which NFS refuses while it
        # is open, and the raised error would otherwise keep it open.
        del root, stage
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
