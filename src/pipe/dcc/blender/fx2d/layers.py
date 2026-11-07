"""Load Layers: which of the shot's publishes an fx2d file imports.

The rows are saved in the .blend, so a version saved before a delivery records what
it was drawn against. The stack Blender imports is written from them.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import PropertyGroup, Scene
from pxr import Gf, Sdf, UsdUtils, Vt

from pipe.core.publish import CURRENT, chosen_layer_path
from pipe.core.sets import current_layer_path as set_layer_path
from pipe.core.shot import current_layer_path

CAMERA = "cam"
# Weakest to strongest, as Houdini's load_layers stacks them. envfx is left out
# because it is being retired.
DEPARTMENTS = (CAMERA, "flo", "anim", "cfx", "fx", "lighting")
# FLO is on from the start because it can move the camera.
NEW_FILE_ROWS = (CAMERA, "flo")
# Where Houdini puts the camera and each set, which the departments' edits target.
CAMERAS_PRIM = Sdf.Path("/cameras")
SETS_PRIM = Sdf.Path("/sets")
# The camera is published in centimetres and everything else in metres. Houdini's
# import_camera scales `/cameras` to match, the stage's only unit conversion.
CAMERA_SCALE = 0.01


class SKD_PG_fx2d_row(PropertyGroup):
    """One row: a department, named for its folder, or a set ShotGrid assigns the shot."""

    # `name` comes with every PropertyGroup.
    is_set: bpy.props.BoolProperty()  # type: ignore
    enabled: bpy.props.BoolProperty(default=True)  # type: ignore
    # CURRENT or a version name such as v007, as Houdini saves it. Sets read current.
    choice: bpy.props.StringProperty(default=CURRENT)  # type: ignore


class SKD_PG_fx2d_layers(PropertyGroup):
    """The rows, and the importer's own type switches, applied on every reload."""

    rows: bpy.props.CollectionProperty(type=SKD_PG_fx2d_row)  # type: ignore
    import_meshes: bpy.props.BoolProperty(name="Meshes", default=True)  # type: ignore
    import_curves: bpy.props.BoolProperty(name="Curves", default=True)  # type: ignore
    import_lights: bpy.props.BoolProperty(name="Lights", default=True)  # type: ignore
    import_volumes: bpy.props.BoolProperty(name="Volumes", default=True)  # type: ignore
    import_materials: bpy.props.BoolProperty(name="Materials", default=True)  # type: ignore


def scene_layers(scene: Scene) -> SKD_PG_fx2d_layers:
    # Registered on Scene by the pipeline addon, which the stubs can't see.
    return scene.fx2d_layers  # type: ignore


def add_missing_rows(layers: SKD_PG_fx2d_layers, set_names: list[str]) -> list[str]:
    """Add a row for every department and assigned set the file lacks; return the sets added.

    A set row is never removed, so a set the artist unticked stays dropped.
    """
    names = {row.name for row in layers.rows}
    for department in DEPARTMENTS:
        if department not in names:
            row = layers.rows.add()
            row.name = department
            row.enabled = department in NEW_FILE_ROWS
    added = [name for name in set_names if name not in names]
    for name in added:
        row = layers.rows.add()
        row.name = name
        row.is_set = True
    return added


def department_layer(shot_root: Path, row: SKD_PG_fx2d_row) -> Path:
    return chosen_layer_path(current_layer_path(shot_root, row.name), row.choice)


def missing_pins(shot_root: Path, layers: SKD_PG_fx2d_layers) -> list[str]:
    """Each department row pinned to a version that is gone, as `anim v003`.

    Unticked rows count: the shot camera is built from every department row.
    """
    return [
        f"{row.name} {row.choice}"
        for row in _departments(layers)
        if row.choice != CURRENT and not department_layer(shot_root, row).is_file()
    ]


def camera_layer(shot_root: Path, layers: SKD_PG_fx2d_layers) -> Path:
    return department_layer(shot_root, layers.rows[CAMERA])


def loaded_layers(shot_root: Path, layers: SKD_PG_fx2d_layers) -> list[Path]:
    """The layers of the ticked departments other than the camera, strongest first.

    A department that hasn't published is left out quietly, as in Houdini.
    """
    return _published(shot_root, [row for row in _over_camera(layers) if row.enabled])


def camera_layers(shot_root: Path, layers: SKD_PG_fx2d_layers) -> list[Path]:
    """The layers of every department other than the camera, ticked or not.

    The shot camera is the one lighting renders through, so unticking a row that
    moves it (FLO on A_110) must not move it back.
    """
    return _published(shot_root, _over_camera(layers))


def set_layers(layers: SKD_PG_fx2d_layers) -> dict[str, Path]:
    """The current layer of every ticked set, by set name."""
    return {
        row.name: set_layer_path(row.name)
        for row in layers.rows
        if row.is_set and row.enabled
    }


def write_stack(
    path: Path, camera: Path, sublayers: list[Path], sets: dict[str, Path]
) -> None:
    """Write one file Blender can import, composed as Houdini's load_layers composes it.

    `sublayers`, strongest first, go over the camera, referenced onto `/cameras`, and
    over each set, payloaded onto `/sets/<name>`. The camera layer gives the stack
    its frame range, in metres like everything but the camera.
    """
    root = Sdf.Layer.CreateAnonymous()
    UsdUtils.CopyLayerMetadata(Sdf.Layer.OpenAsAnonymous(str(camera)), root)
    root.pseudoRoot.SetInfo("metersPerUnit", 1.0)
    root.subLayerPaths[:] = [str(layer) for layer in sublayers]

    cameras = _define(root, CAMERAS_PRIM, "Xform")
    cameras.referenceList.Prepend(Sdf.Reference(str(camera)))
    scale = Sdf.AttributeSpec(cameras, "xformOp:scale", Sdf.ValueTypeNames.Float3)
    scale.default = Gf.Vec3f(CAMERA_SCALE)
    order = Sdf.AttributeSpec(
        cameras,
        "xformOpOrder",
        Sdf.ValueTypeNames.TokenArray,
        variability=Sdf.VariabilityUniform,
    )
    order.default = Vt.TokenArray(["xformOp:scale"])

    for name, layer in sets.items():
        _define(root, SETS_PRIM.AppendChild(name)).payloadList.Prepend(
            Sdf.Payload(str(layer))
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    root.Export(str(path))


def _define(layer: Sdf.Layer, path: Sdf.Path, type_name: str = "") -> Sdf.PrimSpec:
    """A `def` prim at `path`, with `def`s above it: an `over` alone defines nothing."""
    prim = Sdf.CreatePrimInLayer(layer, path)
    prim.typeName = type_name
    for spec_path in path.GetPrefixes():
        layer.GetPrimAtPath(spec_path).specifier = Sdf.SpecifierDef
    return prim


def _departments(layers: SKD_PG_fx2d_layers) -> list[SKD_PG_fx2d_row]:
    rows = {row.name: row for row in layers.rows if not row.is_set}
    return [rows[name] for name in DEPARTMENTS if name in rows]


def _over_camera(layers: SKD_PG_fx2d_layers) -> list[SKD_PG_fx2d_row]:
    return [row for row in _departments(layers) if row.name != CAMERA]


def _published(shot_root: Path, rows: list[SKD_PG_fx2d_row]) -> list[Path]:
    paths = [department_layer(shot_root, row) for row in rows]
    return [path for path in reversed(paths) if path.is_file()]
