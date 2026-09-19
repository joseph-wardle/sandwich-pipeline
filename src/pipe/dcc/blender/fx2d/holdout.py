from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import bpy
from bpy.types import (
    ID,
    Context,
    Event,
    MeshSequenceCacheModifier,
    Operator,
    Scene,
    TransformCacheConstraint,
    ViewLayer,
)
from pxr import Sdf, UsdUtils

from env_sg import DB_Config
from pipe.core.shot import linked_environments
from pipe.core.shotgrid import ShotGrid
from pipe.core.util.paths import get_production_path
from pipe.dcc.blender.fx2d import util

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems


def source_layers(shot_root: Path, source: str) -> list[Path]:
    """The published layers a holdout source is built from."""
    source_usd = util.holdout_usd(shot_root, source)
    if not source_usd.exists():
        return []
    layer = Sdf.Layer.OpenAsAnonymous(str(source_usd))
    return [Path(path) for path in layer.subLayerPaths]


def _write_source(source_usd: Path, layers: list[Path]) -> None:
    """Record a source as one file Blender can import: `layers`, strongest first."""
    root = Sdf.Layer.CreateAnonymous()
    UsdUtils.CopyLayerMetadata(Sdf.Layer.OpenAsAnonymous(str(layers[-1])), root)
    root.subLayerPaths[:] = [str(path) for path in layers]
    source_usd.parent.mkdir(parents=True, exist_ok=True)
    root.Export(str(source_usd))


def _remove_source(scene: Scene, source: str) -> None:
    context_collection = util.child_collection(scene.collection, util.CONTEXT)
    holdout = util.child_collection(context_collection, util.HOLDOUT)
    collection = holdout.children.get(source)
    if collection is None:
        return
    objects = list(collection.all_objects)
    imported: set[ID] = {obj.data for obj in objects if obj.data is not None}
    for obj in objects:
        imported.update(
            user.cache_file
            for user in (*obj.modifiers, *obj.constraints)
            if isinstance(user, (MeshSequenceCacheModifier, TransformCacheConstraint))
            and user.cache_file is not None
        )
    bpy.data.batch_remove(objects)
    bpy.data.batch_remove([block for block in imported if block.users == 0])
    for child in [*collection.children_recursive, collection]:
        bpy.data.collections.remove(child)


def _import_source(
    scene: Scene, view_layer: ViewLayer, source: str, source_usd: Path
) -> int:
    """Import a source into its own collection; returns how many meshes it holds."""
    context_collection = util.child_collection(scene.collection, util.CONTEXT)
    holdout = util.child_collection(context_collection, util.HOLDOUT)
    collection = util.child_collection(holdout, source)

    active_collection = view_layer.active_layer_collection
    # The importer selects what it brings in and makes it the active object, which
    # drops an artist who is drawing out of Draw mode.
    active_object = view_layer.objects.active
    selected = [obj for obj in view_layer.objects if obj.select_get()]
    # Imported objects land in the active collection.
    view_layer.active_layer_collection = (
        view_layer.layer_collection.children[util.CONTEXT]
        .children[util.HOLDOUT]
        .children[source]
    )
    try:
        bpy.ops.wm.usd_import(
            filepath=str(source_usd),
            # Groom guide curves would render as solid grey geometry.
            import_curves=False,
            import_materials=False,
            # The animation file starts at the preroll; the camera sets the range.
            set_frame_range=False,
        )
    finally:
        # Left on the holdout, the artist's next strokes would land in a collection
        # that is never delivered and is wiped by the next import.
        view_layer.active_layer_collection = active_collection
        for obj in view_layer.objects:
            obj.select_set(obj in selected)
        view_layer.objects.active = active_object

    meshes = [obj for obj in collection.all_objects if obj.type == "MESH"]
    for obj in meshes:
        obj.is_holdout = True
    return len(meshes)


def rebuild_holdout(scene: Scene, view_layer: ViewLayer, shot_root: Path) -> str:
    """Make the holdout match the sources chosen on disk; returns what to tell the artist."""
    imported: list[str] = []
    for source in util.HOLDOUT_SOURCES:
        _remove_source(scene, source)
        source_usd = util.holdout_usd(shot_root, source)
        if not source_usd.exists():
            continue
        meshes = _import_source(scene, view_layer, source, source_usd)
        imported.append(f"{meshes} {source} meshes")
    if not imported:
        return "The file has no holdout."

    # Grease Pencil objects from the Add menu start with In Front on, which
    # draws them over the holdout no matter where they sit in depth.
    in_front = [
        obj for obj in scene.objects if obj.type == "GREASEPENCIL" and obj.show_in_front
    ]
    for obj in in_front:
        obj.show_in_front = False

    message = f"Imported {' and '.join(imported)} as the holdout."
    if in_front:
        names = ", ".join(obj.name for obj in in_front)
        message += f" Turned off In Front on: {names}."
    return message


def _set_layers(shot_code: str) -> list[Path]:
    """The `main.usd` of every set ShotGrid links to the shot."""
    shot = ShotGrid.connect(DB_Config).get_shot(code=shot_code)
    return [
        get_production_path() / env.environment_path / "main.usd"
        for env in linked_environments(shot)
    ]


# What the dialog may offer, by property name. Looked up once when it opens, because
# `draw` runs on every redraw and the set lookup asks ShotGrid.
_PUBLISHED: dict[str, bool] = {}


class SKD_OT_fx2d_import_holdout(Operator):
    """Choose the published geometry that cuts the drawing.

    Running it again replaces the previous import, and Refresh rebuilds the same
    choice. The backdrop is enough for most shots, and a holdout is for strokes that
    pass behind something.
    """

    bl_idname = "skd.fx2d_import_holdout"
    bl_label = "Import Holdout"

    characters: bpy.props.BoolProperty(name="Characters", default=True)  # type: ignore
    cfx: bpy.props.BoolProperty(name="With CFX: cloth and mesh hair")  # type: ignore
    sets: bpy.props.BoolProperty(name="Set")  # type: ignore

    @classmethod
    def poll(cls, context: Context) -> bool:
        return util.poll_fx2d_file(cls)

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        assert shot_root is not None
        chosen = {
            source: source_layers(shot_root, source) for source in util.HOLDOUT_SOURCES
        }
        # A file that already has a holdout opens on what it has.
        if any(chosen.values()):
            self.characters = bool(chosen[util.CHARACTERS])
            self.cfx = util.cfx_usd(shot_root) in chosen[util.CHARACTERS]
            self.sets = bool(chosen[util.SET])
        set_layers = _set_layers(shot_root.name)
        _PUBLISHED.update(
            characters=util.anim_usd(shot_root).exists(),
            cfx=util.cfx_usd(shot_root).exists(),
            sets=bool(set_layers) and all(path.exists() for path in set_layers),
        )
        return context.window_manager.invoke_props_dialog(self)  # type: ignore

    def draw(self, context: Context) -> None:
        layout = self.layout
        assert layout is not None
        for name, published in _PUBLISHED.items():
            row = layout.row()
            row.enabled = published and (name != "cfx" or self.characters)
            row.prop(self, name)
        layout.label(text="Hair grown at render time cannot be a holdout.")

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene, view_layer = context.scene, context.view_layer
        assert shot_root is not None and scene is not None and view_layer is not None

        layers: dict[str, list[Path]] = {}
        if self.characters:
            anim = [util.anim_usd(shot_root)]
            layers[util.CHARACTERS] = (
                [util.cfx_usd(shot_root), *anim] if self.cfx else anim
            )
        if self.sets:
            layers[util.SET] = _set_layers(shot_root.name)
            if not layers[util.SET]:
                self.report(
                    {"ERROR"},
                    f"Could not import the set because {shot_root.name} has no set "
                    "linked in ShotGrid. Nothing was changed.",
                )
                return {"CANCELLED"}
        unreadable = util.unreadable(
            path for paths in layers.values() for path in paths
        )
        if unreadable:
            self.report(
                {"ERROR"},
                "Could not import the holdout because these published files are "
                f"missing or cannot be read: {', '.join(unreadable)}. A department may "
                "be publishing; try again in a few minutes. Nothing was changed.",
            )
            return {"CANCELLED"}

        for source in util.HOLDOUT_SOURCES:
            source_usd = util.holdout_usd(shot_root, source)
            if source in layers:
                _write_source(source_usd, layers[source])
            else:
                source_usd.unlink(missing_ok=True)
        self.report({"INFO"}, rebuild_holdout(scene, view_layer, shot_root))
        return {"FINISHED"}
