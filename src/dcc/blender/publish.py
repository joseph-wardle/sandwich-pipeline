import bpy
from bpy.types import Context, Operator
from env_sg import DB_Config

from core.asset import paths_for_asset
from dcc.blender.util.register import blender_operator
from core.shotgrid import ShotGrid


@blender_operator(add_to_menu=True)
class PIPELINE_OT_publish_asset(Operator):
    """Publish the selected meshes as the asset model USD file."""

    bl_idname = "pipeline.publish_asset"
    bl_label = "Publish Selected"

    def invoke(self, context: Context, event):
        conn = ShotGrid.connect(DB_Config)
        open_asset_name = bpy.context.scene.pipeline_asset.name  # type: ignore
        if open_asset_name:
            asset = conn.get_asset(name=open_asset_name)
            paths = paths_for_asset(asset)
            self._target_path = paths.publish_source_model_usd.resolve()

        else:
            self.report(
                {"ERROR"}, "No asset metadata found in blend file. Cannot publish."
            )

        return context.window_manager.invoke_confirm(  # type: ignore
            self, event, message="Publish Asset Model USD?"
        )

    def execute(self, context: Context):
        path = self._target_path
        bpy.ops.wm.usd_export(
            filepath=str(path),
            check_existing=False,
            selected_objects_only=True,
            export_lights=False,
            export_cameras=False,
        )
        self.report({"INFO"}, f"Publish Successful! USD file written to {path}")
        return {"FINISHED"}
