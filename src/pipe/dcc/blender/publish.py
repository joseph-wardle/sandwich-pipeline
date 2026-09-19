from __future__ import annotations

from typing import TYPE_CHECKING

import bpy
from bpy.types import Context, Event, Operator

from env_sg import DB_Config
from pipe.core.asset import paths_for_asset
from pipe.core.shotgrid import ShotGrid

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

GEO_VARIANT = "main"


class PIPELINE_OT_publish_asset(Operator):
    """Publish the selected meshes as the asset's main geometry variant."""

    bl_idname = "pipeline.publish_asset"
    bl_label = "Publish Selected"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not context.scene.pipeline_asset.name:  # type: ignore
            cls.poll_message_set(
                "This file is not a pipeline asset file. Open the asset with "
                "Pipeline > Open Asset, then bring your model into that file."
            )
            return False
        if not any(obj.type == "MESH" for obj in context.selected_objects):
            # Exporting an empty selection would replace the published model with
            # an empty file.
            cls.poll_message_set("Select the meshes to publish first.")
            return False
        return True

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        return context.window_manager.invoke_confirm(  # type: ignore
            self, event, message="Publish Asset Model USD?"
        )

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        conn = ShotGrid.connect(DB_Config)
        asset = conn.get_asset(name=context.scene.pipeline_asset.name)  # type: ignore
        path = paths_for_asset(asset).publish_source_variant_usd(GEO_VARIANT).resolve()

        # Blender works Z-up in metres; the show is Y-up in centimetres.
        bpy.ops.wm.usd_export(
            filepath=str(path),
            check_existing=False,
            selected_objects_only=True,
            export_lights=False,
            export_cameras=False,
            convert_orientation=True,
            export_global_forward_selection="NEGATIVE_Z",
            export_global_up_selection="Y",
            convert_scene_units="CENTIMETERS",
        )
        self.report({"INFO"}, f"Publish Successful! USD file written to {path}")
        return {"FINISHED"}
