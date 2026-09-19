from __future__ import annotations

from typing import TYPE_CHECKING

import bpy
from bpy.types import (
    ID,
    Context,
    MeshSequenceCacheModifier,
    Operator,
    TransformCacheConstraint,
)

from pipe.dcc.blender.fx2d import util

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems


class PIPELINE_OT_fx2d_import_holdout(Operator):
    """Import the shot's published animation as geometry that cuts the drawing.

    Running it again replaces the previous import, which is how a republished
    animation is picked up.
    """

    bl_idname = "pipeline.fx2d_import_holdout"
    bl_label = "Import Holdout"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if util.shot_root() is None:
            cls.poll_message_set(util.NOT_FX2D_FILE)
            return False
        return True

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene, view_layer = context.scene, context.view_layer
        assert shot_root is not None and scene is not None and view_layer is not None

        anim_usd = shot_root / "anim" / "usd" / "main.usd"
        if not anim_usd.exists():
            self.report(
                {"ERROR"},
                f"Could not import the holdout because the shot has no published "
                f"animation at {anim_usd}.",
            )
            return {"CANCELLED"}

        context_collection = util.child_collection(scene.collection, util.CONTEXT)
        holdout = util.child_collection(context_collection, util.HOLDOUT)
        # Imported object names are not stable ("body_geo.001" appears on a first
        # import), so a re-import replaces everything instead of matching by name.
        objects = list(holdout.all_objects)
        # Removing an object leaves its mesh and cache file behind. Only what the
        # import made is removed: a purge would also take the artist's unused data.
        imported: set[ID] = {obj.data for obj in objects if obj.data is not None}
        for obj in objects:
            imported.update(
                user.cache_file
                for user in (*obj.modifiers, *obj.constraints)
                if isinstance(
                    user, (MeshSequenceCacheModifier, TransformCacheConstraint)
                )
                and user.cache_file is not None
            )
        bpy.data.batch_remove(objects)
        bpy.data.batch_remove([block for block in imported if block.users == 0])
        for child in list(holdout.children_recursive):
            bpy.data.collections.remove(child)

        active = view_layer.active_layer_collection
        # Imported objects land in the active collection.
        view_layer.active_layer_collection = view_layer.layer_collection.children[
            util.CONTEXT
        ].children[util.HOLDOUT]
        bpy.ops.wm.usd_import(
            filepath=str(anim_usd),
            # Groom guide curves would render as solid grey geometry.
            import_curves=False,
            import_materials=False,
            # The animation file starts at the preroll; the camera sets the range.
            set_frame_range=False,
        )
        view_layer.active_layer_collection = active

        meshes = [obj for obj in holdout.all_objects if obj.type == "MESH"]
        for obj in meshes:
            obj.is_holdout = True

        # Grease Pencil objects from the Add menu start with In Front on, which
        # draws them over the holdout no matter where they sit in depth.
        in_front = [
            obj
            for obj in scene.objects
            if obj.type == "GREASEPENCIL" and obj.show_in_front
        ]
        for obj in in_front:
            obj.show_in_front = False

        message = f"Imported {len(meshes)} holdout meshes."
        if in_front:
            names = ", ".join(obj.name for obj in in_front)
            message += f" Turned off In Front on: {names}."
        self.report({"INFO"}, message)
        return {"FINISHED"}
