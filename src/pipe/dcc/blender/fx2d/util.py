"""What every fx2d tool needs to know about the open file."""

from __future__ import annotations

import re
from pathlib import Path

import bpy
from bpy.types import Collection, Scene, ViewLayer

DEPARTMENT = "fx2d"
FILE_NAME = "fx2d.blend"

# Pipeline-owned collections. Everything else at the top level is an effect layer.
CONTEXT = "context"
HOLDOUT = "holdout"
DEFAULT_LAYER = "main"
LAYER_PREFIX = "fx2d_"

VERSION = re.compile(r"^V_(\d+)$")

NOT_FX2D_FILE = "This is not an fx2d file. Open one with Pipeline > Open Shot (fx2d)."


def shot_root() -> Path | None:
    """The open file's shot folder, or None when the file is not `<shot>/fx2d/fx2d.blend`."""
    path = Path(bpy.data.filepath)
    if path.name != FILE_NAME or path.parent.name != DEPARTMENT:
        return None
    return path.parents[1]


def cache_root(shot_root: Path) -> Path:
    """The shot's folder on /cache, which mirrors its path on /groups."""
    return Path("/cache", *shot_root.parts[2:])


def render_root(shot_root: Path) -> Path:
    """Where Nuke's auto-read looks; once this exists it stops looking anywhere else."""
    return cache_root(shot_root) / "render"


def layer_dir(shot_root: Path, collection: Collection) -> Path:
    return render_root(shot_root) / f"{LAYER_PREFIX}{collection.name}"


def backdrop_root(shot_root: Path) -> Path:
    """Beside `render/`, not inside it, so comp never mistakes a proxy for a layer."""
    return cache_root(shot_root) / DEPARTMENT / "backdrop"


def child_collection(parent: Collection, name: str) -> Collection:
    existing = parent.children.get(name)
    if existing is not None:
        return existing
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


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
