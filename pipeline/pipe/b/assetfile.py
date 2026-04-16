import bpy
from bpy.types import Context
from env_sg import DB_Config

from pipe.asset import paths_for_asset
from pipe.b.register import blender_class, blender_operator
from pipe.db import DB, DBInterface
from pipe.struct.db import Asset


@blender_class
class PipelineAssetProps(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()  # type: ignore
    display_name: bpy.props.StringProperty()  # type: ignore


def get_asset_names():
    conn = DB.Get(DB_Config)
    asset_names = conn.get_entity_code_list(
        Asset,
        sorted=True,
        child_mode=DBInterface.ChildQueryMode.ROOTS,
    )
    return asset_names


@blender_operator()
class PIPELINE_OT_open_asset(bpy.types.Operator):
    bl_idname = "pipeline.open_asset"
    bl_label = "Open/Create Asset File"
    asset: Asset
    asset_name: bpy.props.StringProperty()  # type: ignore

    def invoke(self, context: Context, event):
        conn = DB.Get(DB_Config)
        self.asset = conn.get_asset_by_display_name(self.asset_name)
        paths = paths_for_asset(self.asset)

        self._target_path = paths.blender_model_path.resolve()
        if not self._target_path.exists():
            message = f"Create asset file for: {self.asset_name}?"
        else:
            message = f"Open asset file for: {self.asset_name}?"

        return context.window_manager.invoke_confirm(self, event, message=message)  # type: ignore

    def execute(self, context):
        path = self._target_path

        if path.exists():
            bpy.ops.wm.open_mainfile(filepath=str(path))
        else:
            bpy.ops.wm.read_homefile(app_template="")
            bpy.context.scene.pipeline_asset.name = self.asset.name  # type: ignore
            bpy.context.scene.pipeline_asset.display_name = self.asset.display_name  # type: ignore
            bpy.ops.wm.save_as_mainfile(filepath=str(path))
        return {"FINISHED"}


def get_asset_items(self, context):
    # This must return a list of tuples: (identifier, label, description)
    assets = get_asset_names()
    return [(asset, asset, f"Open {asset}") for asset in assets]


def on_asset_selected(self, context):
    """This triggers as soon as the user hits Enter or clicks an item."""
    bpy.ops.pipeline.open_asset("INVOKE_DEFAULT", asset_name=self.asset_id)  # type: ignore


@blender_operator(add_to_menu=True)
class PIPELINE_OT_search_and_open_asset(bpy.types.Operator):
    """Open a search menu to find and open an asset file."""

    bl_idname = "pipeline.search_assets"
    bl_label = "Open Asset"
    bl_property = "asset_id"

    asset_id: bpy.props.EnumProperty(  # type: ignore
        name="Asset", items=get_asset_items
    )

    def execute(self, context):
        bpy.ops.pipeline.open_asset("INVOKE_DEFAULT", asset_name=self.asset_id)  # type: ignore
        return {"FINISHED"}

    def invoke(self, context, event):
        # This triggers the search visual immediately
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}
