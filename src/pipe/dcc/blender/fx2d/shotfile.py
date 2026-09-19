from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import bpy
from bpy.types import Context, Event, Operator
from pxr import Usd

from env_sg import DB_Config
from pipe.core.shot import shot_root_path
from pipe.core.shotgrid import ShotGrid
from pipe.dcc.blender.fx2d import scene as fx2d_scene

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

# Blender keeps pointers into the strings an enum callback returns, so the list has
# to outlive the call or the search popup shows garbage.
_SHOT_ITEMS: list[tuple[str, str, str]] = []


def _shot_items(self: Operator, context: Context | None) -> list[tuple[str, str, str]]:
    conn = ShotGrid.connect(DB_Config)
    codes = sorted(shot.code for shot in conn.find_shots() if shot.code)
    _SHOT_ITEMS[:] = [(code, code, "") for code in codes]
    return _SHOT_ITEMS


def _create(camera_usd: Path, path: Path) -> None:
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    assert scene is not None and view_layer is not None

    context_collection = fx2d_scene.child_collection(
        scene.collection, fx2d_scene.CONTEXT
    )
    fx2d_scene.make_active(view_layer, context_collection)
    # The importer also sets the scene frame range from the camera file, which the
    # camera publish writes as the cut plus tails.
    # Loading a file from inside an operator leaves the context with no window,
    # and the importer refuses to run without one.
    window = bpy.context.window_manager.windows[0]  # type: ignore
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.usd_import(filepath=str(camera_usd))
    scene.camera = next(
        obj for obj in context_collection.all_objects if obj.type == "CAMERA"
    )
    scene.render.fps = round(Usd.Stage.Open(str(camera_usd)).GetTimeCodesPerSecond())
    fx2d_scene.apply_render_settings(scene, view_layer)

    layer = fx2d_scene.child_collection(scene.collection, fx2d_scene.DEFAULT_LAYER)
    # Anything drawn outside a layer collection would render into every layer.
    fx2d_scene.make_active(view_layer, layer)

    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))


class PIPELINE_OT_fx2d_open_shot(Operator):
    """Open a shot's fx2d file, creating it from the shot camera the first time."""

    bl_idname = "pipeline.fx2d_open_shot"
    bl_label = "Open Shot (fx2d)"
    bl_property = "shot_code"

    shot_code: bpy.props.EnumProperty(name="Shot", items=_shot_items)  # type: ignore

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        context.window_manager.invoke_search_popup(self)  # type: ignore
        return {"RUNNING_MODAL"}

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        # Opening a file from Python skips Blender's own "save changes?" prompt.
        if bpy.data.is_dirty:
            self.report(
                {"ERROR"},
                "Could not open the shot because this file has unsaved changes. "
                "Save it or start a new file, then try again.",
            )
            return {"CANCELLED"}

        conn = ShotGrid.connect(DB_Config)
        shot_root = shot_root_path(conn.get_shot(code=self.shot_code))
        path = shot_root / fx2d_scene.DEPARTMENT / fx2d_scene.FILE_NAME
        if path.exists():
            bpy.ops.wm.open_mainfile(filepath=str(path))
            return {"FINISHED"}

        camera_usd = shot_root / "cam" / "cam.usd"
        if not camera_usd.exists():
            self.report(
                {"ERROR"},
                f"Could not create the fx2d file because {self.shot_code} has no "
                f"published camera at {camera_usd}. Ask layout to publish the camera.",
            )
            return {"CANCELLED"}
        _create(camera_usd, path)
        return {"FINISHED"}
