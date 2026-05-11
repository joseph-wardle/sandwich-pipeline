import logging

import bpy
from bpy.types import Operator
from bpy.utils import register_class, unregister_class
from dcc.blender.assetfile import PipelineAssetProps
from dcc.blender.util.register import get_decorated_classes, get_decorated_operators

bl_info = {"name": "Sandwich Pipeline", "blender": (5, 0, 1), "category": "Pipeline"}

registered_classes: set[
    type[
        bpy.types.Panel
        | bpy.types.UIList
        | bpy.types.Menu
        | bpy.types.Header
        | bpy.types.Operator
        | bpy.types.KeyingSetInfo
        | bpy.types.RenderEngine
        | bpy.types.AssetShelf
        | bpy.types.FileHandler
        | bpy.types.PropertyGroup
        | bpy.types.AddonPreferences
        | bpy.types.NodeTree
        | bpy.types.Node
        | bpy.types.NodeSocket
    ]
] = set()
menu_operators: list[type[Operator]] = []

log = logging.getLogger("dcc.blender.addon")


class PIPELINE_PT_tools(bpy.types.Panel):
    bl_label = "Pipeline Tools"
    bl_idname = "PIPELINE_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pipeline"

    def draw(self, context):
        layout = self.layout
        if layout is None:
            return
        for operator in menu_operators:
            layout.operator(operator.bl_idname)


class PIPELINE_MT_menu(bpy.types.Menu):
    bl_label = "Pipeline"
    bl_idname = "PIPELINE_MT_menu"

    def draw(self, context):
        layout = self.layout
        if layout is None:
            return
        for operator in menu_operators:
            layout.operator(operator.bl_idname)


def draw_pipeline(self, context):
    self.layout.menu(PIPELINE_MT_menu.bl_idname)


def register():
    global registered_classes

    operators_to_register = get_decorated_operators()
    for operator_description in operators_to_register:
        operator = operator_description.operator
        register_class(operator)
        registered_classes.add(operator)
        if operator_description.add_to_menu:
            menu_operators.append(operator)
        log.debug(f"{operator} registered as operator.")

    classes_to_register = get_decorated_classes()
    for cls in classes_to_register:
        register_class(cls)
        registered_classes.add(cls)
        log.debug(f"{operator} registered as Blender class.")

    bpy.types.Scene.pipeline_asset = bpy.props.PointerProperty(type=PipelineAssetProps)  # type: ignore
    bpy.utils.register_class(PIPELINE_MT_menu)
    bpy.utils.register_class(PIPELINE_PT_tools)
    bpy.types.TOPBAR_MT_editor_menus.append(draw_pipeline)
    log.info("Pipeline addon loaded!")


def unregister():
    bpy.types.TOPBAR_MT_editor_menus.remove(draw_pipeline)
    bpy.utils.unregister_class(PIPELINE_PT_tools)
    bpy.utils.unregister_class(PIPELINE_MT_menu)
    for cls in registered_classes:
        unregister_class(cls)
    menu_operators.clear()
    del bpy.types.Scene.pipeline_asset  # type: ignore
    log.info("Pipeline addon unloaded!")


if __name__ == "__main__":
    register()
