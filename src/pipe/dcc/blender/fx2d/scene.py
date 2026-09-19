"""What every fx2d tool needs to know about the open file."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Collection, LayerCollection, Scene, ViewLayer

DEPARTMENT = "fx2d"
FILE_NAME = "fx2d.blend"

# Pipeline-owned collections. Everything else at the top level is an effect layer.
CONTEXT = "context"
HOLDOUT = "holdout"
DEFAULT_LAYER = "fx2d"

NOT_FX2D_FILE = "This is not an fx2d file. Open one with Pipeline > Open Shot (fx2d)."


def shot_root() -> Path | None:
    """The open file's shot folder, or None when the file is not `<shot>/fx2d/fx2d.blend`.

    The path is the only record of which shot a file belongs to, so a copy saved
    elsewhere stops being an fx2d file instead of delivering to the wrong shot.
    """
    path = Path(bpy.data.filepath)
    if path.name != FILE_NAME or path.parent.name != DEPARTMENT:
        return None
    return path.parents[1]


def child_collection(parent: Collection, name: str) -> Collection:
    existing = parent.children.get(name)
    if existing is not None:
        return existing
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def _layer_collection(root: LayerCollection, name: str) -> LayerCollection | None:
    if root.name == name:
        return root
    for child in root.children:
        found = _layer_collection(child, name)
        if found is not None:
            return found
    return None


def make_active(view_layer: ViewLayer, collection: Collection) -> None:
    """New and imported objects land in the active collection."""
    layer_collection = _layer_collection(view_layer.layer_collection, collection.name)
    if layer_collection is not None:
        view_layer.active_layer_collection = layer_collection


def apply_render_settings(scene: Scene, view_layer: ViewLayer) -> None:
    """The settings an effect layer is rendered with.

    EEVEE because Workbench ignores holdouts. The Z pass is what lets holdout
    geometry cut Grease Pencil strokes; without it strokes draw over everything.
    """
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = True
    view_layer.use_pass_z = True
    # Matches the RenderMan renders the layer is composited over.
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    image = scene.render.image_settings
    image.file_format = "OPEN_EXR"
    image.color_mode = "RGBA"
    image.color_depth = "16"
