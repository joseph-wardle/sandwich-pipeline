import logging

import bpy
from bpy.types import Context, Menu, Operator, Panel, PropertyGroup
from bpy.utils import register_class, unregister_class

from pipe.dcc.blender.assetfile import (
    PIPELINE_OT_open_asset,
    PIPELINE_OT_search_and_open_asset,
    PipelineAssetProps,
)
from pipe.dcc.blender.fx2d import (
    PIPELINE_OT_fx2d_deliver,
    PIPELINE_OT_fx2d_import_holdout,
    PIPELINE_OT_fx2d_open_shot,
)
from pipe.dcc.blender.publish import PIPELINE_OT_publish_asset

bl_info = {"name": "Sandwich Pipeline", "blender": (5, 0, 1), "category": "Pipeline"}

log = logging.getLogger("pipe.dcc.blender.addon")

# Shown in the Pipeline menu and the N-panel, in this order.
MENU_OPERATORS: tuple[type[Operator], ...] = (
    PIPELINE_OT_search_and_open_asset,
    PIPELINE_OT_publish_asset,
    PIPELINE_OT_fx2d_open_shot,
    PIPELINE_OT_fx2d_import_holdout,
    PIPELINE_OT_fx2d_deliver,
)


class PIPELINE_PT_tools(Panel):
    bl_label = "Pipeline Tools"
    bl_idname = "PIPELINE_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pipeline"

    def draw(self, context: Context) -> None:
        layout = self.layout
        if layout is None:
            return
        for operator in MENU_OPERATORS:
            layout.operator(operator.bl_idname)


class PIPELINE_MT_menu(Menu):
    bl_label = "Pipeline"
    bl_idname = "PIPELINE_MT_menu"

    def draw(self, context: Context) -> None:
        layout = self.layout
        if layout is None:
            return
        for operator in MENU_OPERATORS:
            layout.operator(operator.bl_idname)


# Every class the addon registers. A new operator does nothing until it is listed
# here. PipelineAssetProps comes first because the Scene property below points at it.
CLASSES: tuple[type[Operator | Menu | Panel | PropertyGroup], ...] = (
    PipelineAssetProps,
    PIPELINE_OT_open_asset,
    *MENU_OPERATORS,
    PIPELINE_MT_menu,
    PIPELINE_PT_tools,
)


def draw_pipeline(self: Menu, context: Context) -> None:
    layout = self.layout
    if layout is None:
        return
    layout.menu(PIPELINE_MT_menu.bl_idname)


def register() -> None:
    for cls in CLASSES:
        register_class(cls)
    bpy.types.Scene.pipeline_asset = bpy.props.PointerProperty(type=PipelineAssetProps)  # type: ignore
    bpy.types.TOPBAR_MT_editor_menus.append(draw_pipeline)
    log.info("Pipeline addon loaded!")


def unregister() -> None:
    bpy.types.TOPBAR_MT_editor_menus.remove(draw_pipeline)
    del bpy.types.Scene.pipeline_asset  # type: ignore
    for cls in reversed(CLASSES):
        unregister_class(cls)
    log.info("Pipeline addon unloaded!")


if __name__ == "__main__":
    register()
