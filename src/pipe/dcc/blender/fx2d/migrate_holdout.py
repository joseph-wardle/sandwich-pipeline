"""Files made before Load Layers: turn what Import Holdout left into rows.

Called only from reload. Delete this module and its two calls there once no shot has
an `fx2d/holdout/` folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import bpy
from bpy.types import (
    ID,
    Collection,
    MeshSequenceCacheModifier,
    Scene,
    TransformCacheConstraint,
)
from pxr import Sdf

from pipe.dcc.blender.fx2d import layers, util

HOLDOUT = "holdout"
# Where the artist's own work found in the context collection goes. Kept out of
# delivery, as it was in the context collection, and out of the holdout.
MOVED = "from_context"


def add_rows(
    shot_root: Path, chosen: layers.SKD_PG_fx2d_layers, assigned: list[str]
) -> None:
    """Give a file that has never reloaded the rows its Import Holdout choice asks for."""
    folder = shot_root / util.DEPARTMENT / HOLDOUT
    if chosen.rows or not folder.is_dir():
        return
    # Each source file lists the layers it was made from, which are named for their
    # department or set: anim.usd and cfx.usd, or shop_interior.usda.
    chosen_names: set[str] = set()
    for source in folder.glob("*.usda"):
        # Held while its sublayers are read: they die with the layer.
        layer = Sdf.Layer.OpenAsAnonymous(str(source))
        chosen_names.update(Path(sublayer).stem for sublayer in layer.subLayerPaths)
    sets = [name for name in chosen_names if name not in layers.DEPARTMENTS]
    layers.add_missing_rows(
        chosen, [*assigned, *(s for s in sets if s not in assigned)]
    )
    for row in chosen.rows:
        if row.name in chosen_names:
            row.enabled = True


def remove_holdout(scene: Scene, shot_root: Path) -> str:
    """Remove what Import Holdout left in the file and on disk; returns what to tell the artist."""
    # Every file made before Load Layers parents the shot camera under a
    # centimetre-scaled empty it read cam.usd through. Reload unparents it.
    cameras = scene.camera.parent if scene.camera is not None else None
    if cameras is None:
        return ""
    context_collection = util.child_collection(scene.collection, util.CONTEXT)

    holdout = context_collection.children.get(HOLDOUT)
    if holdout is not None:
        _remove_collection(holdout)
    # The collection's Holdout flag would hide anything drawn here.
    artist = [
        obj for obj in context_collection.objects if obj not in (cameras, scene.camera)
    ]
    if artist:
        moved = util.child_collection(scene.collection, MOVED)
        moved.hide_render = True
        for obj in artist:
            moved.objects.link(obj)
            context_collection.objects.unlink(obj)
    bpy.data.objects.remove(cameras)
    # Files that never imported a holdout have no folder.
    folder = shot_root / util.DEPARTMENT / HOLDOUT
    if folder.is_dir():
        shutil.rmtree(folder)

    message = " Converted the file from Import Holdout."
    if artist:
        message += (
            f" Moved {', '.join(obj.name for obj in artist)} out of the context "
            f"collection into {MOVED}, which is not delivered."
        )
    return message


def _remove_collection(collection: Collection) -> None:
    """Remove a collection, everything in it, and the data and caches only it used."""
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
