import logging

import bpy
from bpy.types import Context, Menu, Operator, Panel, PropertyGroup
from bpy.utils import register_class, unregister_class

from pipe.dcc.blender.assetfile import (
    SKD_OT_open_asset,
    SKD_OT_search_and_open_asset,
    PipelineAssetProps,
)
from pipe.dcc.blender.fx2d import (
    SKD_OT_fx2d_deliver,
    SKD_OT_fx2d_load_layers,
    SKD_OT_fx2d_open_shot,
    SKD_OT_fx2d_playblast,
    SKD_OT_fx2d_playblast_discard,
    SKD_OT_fx2d_playblast_progress,
    SKD_OT_fx2d_playblast_send,
    SKD_OT_fx2d_playblast_watch,
    SKD_OT_fx2d_reload,
    SKD_OT_fx2d_reload_on_open,
    SKD_OT_fx2d_set_backdrop,
    SKD_PG_fx2d_layers,
    SKD_PG_fx2d_playblast,
    SKD_PG_fx2d_row,
    SKD_PT_fx2d_playblast,
    reload_on_open,
    remove_saved_preview,
)
from pipe.dcc.blender.publish import SKD_OT_publish_asset

bl_info = {"name": "Sandwich Pipeline", "blender": (5, 0, 1), "category": "Pipeline"}

log = logging.getLogger("pipe.dcc.blender.addon")

ASSET_OPERATORS: tuple[type[Operator], ...] = (
    SKD_OT_search_and_open_asset,
    SKD_OT_publish_asset,
)
SHOT_OPERATORS: tuple[type[Operator], ...] = (
    SKD_OT_fx2d_open_shot,
    SKD_OT_fx2d_set_backdrop,
    SKD_OT_fx2d_load_layers,
    SKD_OT_fx2d_reload,
    SKD_OT_fx2d_playblast,
    SKD_OT_fx2d_deliver,
)
ASSET_LABEL = "Asset"
SHOT_LABEL = "Shot (fx2d)"


class _Tools(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SKD"
    operators: tuple[type[Operator], ...]

    def draw(self, context: Context) -> None:
        layout = self.layout
        if layout is None:
            return
        for operator in self.operators:
            layout.operator(operator.bl_idname)


class SKD_PT_asset(_Tools):
    bl_label = ASSET_LABEL
    bl_idname = "SKD_PT_asset"
    operators = ASSET_OPERATORS


class SKD_PT_shot(_Tools):
    bl_label = SHOT_LABEL
    bl_idname = "SKD_PT_shot"
    operators = SHOT_OPERATORS


class SKD_MT_menu(Menu):
    bl_label = "SKD"
    bl_idname = "SKD_MT_menu"

    def draw(self, context: Context) -> None:
        layout = self.layout
        if layout is None:
            return
        layout.label(text=ASSET_LABEL)
        for operator in ASSET_OPERATORS:
            layout.operator(operator.bl_idname)
        layout.separator()
        layout.label(text=SHOT_LABEL)
        for operator in SHOT_OPERATORS:
            layout.operator(operator.bl_idname)


# Every class the addon registers. A new operator does nothing until it is listed
# here. Property groups come before whatever points at them: the Scene properties
# below, and the fx2d rows inside the fx2d layers.
CLASSES: tuple[type[Operator | Menu | Panel | PropertyGroup], ...] = (
    PipelineAssetProps,
    SKD_PG_fx2d_row,
    SKD_PG_fx2d_layers,
    SKD_PG_fx2d_playblast,
    SKD_OT_open_asset,
    SKD_OT_fx2d_reload_on_open,
    SKD_OT_fx2d_playblast_watch,
    SKD_OT_fx2d_playblast_discard,
    SKD_OT_fx2d_playblast_send,
    SKD_OT_fx2d_playblast_progress,
    *ASSET_OPERATORS,
    *SHOT_OPERATORS,
    SKD_MT_menu,
    SKD_PT_asset,
    SKD_PT_shot,
    SKD_PT_fx2d_playblast,
)


def draw_pipeline(self: Menu, context: Context) -> None:
    layout = self.layout
    if layout is None:
        return
    layout.menu(SKD_MT_menu.bl_idname)


def register() -> None:
    for cls in CLASSES:
        register_class(cls)
    bpy.types.Scene.pipeline_asset = bpy.props.PointerProperty(type=PipelineAssetProps)  # type: ignore
    bpy.types.Scene.fx2d_layers = bpy.props.PointerProperty(type=SKD_PG_fx2d_layers)  # type: ignore
    bpy.types.WindowManager.skd_fx2d_playblast = bpy.props.PointerProperty(  # type: ignore
        type=SKD_PG_fx2d_playblast
    )
    bpy.types.TOPBAR_MT_editor_menus.append(draw_pipeline)
    bpy.app.handlers.load_post.append(reload_on_open)
    bpy.app.handlers.load_post.append(remove_saved_preview)
    log.info("Pipeline addon loaded!")


def unregister() -> None:
    bpy.app.handlers.load_post.remove(remove_saved_preview)
    bpy.app.handlers.load_post.remove(reload_on_open)
    bpy.types.TOPBAR_MT_editor_menus.remove(draw_pipeline)
    del bpy.types.WindowManager.skd_fx2d_playblast  # type: ignore
    del bpy.types.Scene.fx2d_layers  # type: ignore
    del bpy.types.Scene.pipeline_asset  # type: ignore
    for cls in reversed(CLASSES):
        unregister_class(cls)
    log.info("Pipeline addon unloaded!")


if __name__ == "__main__":
    register()
