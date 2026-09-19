from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import bpy
from bpy.types import Camera, Context, Event, Operator
from pxr import Usd

from env_sg import DB_Config
from pipe.core.shot import shot_root_path
from pipe.core.shotgrid import ShotGrid
from pipe.dcc.blender.fx2d import backdrop
from pipe.dcc.blender.fx2d import util

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)

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

    context_collection = util.child_collection(scene.collection, util.CONTEXT)
    layer = util.child_collection(scene.collection, util.DEFAULT_LAYER)
    layer_collections = view_layer.layer_collection.children
    # Imported objects land in the active collection.
    view_layer.active_layer_collection = layer_collections[util.CONTEXT]
    # Loading a file from inside an operator leaves the context with no window,
    # and the importer refuses to run without one.
    window = bpy.context.window_manager.windows[0]  # type: ignore
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.usd_import(filepath=str(camera_usd))
    scene.camera = next(
        obj for obj in context_collection.all_objects if obj.type == "CAMERA"
    )
    # A new scene sits on frame 1, outside the shot, where a backdrop has no frame.
    scene.frame_current = scene.frame_start
    # The importer sets the frame range from the camera file but not the fps.
    scene.render.fps = round(Usd.Stage.Open(str(camera_usd)).GetTimeCodesPerSecond())
    util.apply_render_settings(scene, view_layer)

    # Anything drawn outside a layer collection would render into every layer.
    view_layer.active_layer_collection = layer_collections[layer.name]

    # The backdrop only shows when looking through the shot camera.
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.region_3d.view_perspective = "CAMERA"  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    # Save As rewrites file paths relative to the .blend by default, which turns
    # the /cache backdrop into a long ../ chain that breaks if either side moves.
    bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=False)


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
        path = shot_root / util.DEPARTMENT / util.FILE_NAME
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
        created = f"Created the fx2d file for {self.shot_code}."

        # Beauty is what a new file shows until the artist picks another layer.
        frames = backdrop.latest_frames(util.render_root(shot_root) / "beauty")
        if not frames:
            self.report(
                {"INFO"},
                f"{created} The shot has no beauty render yet, so no backdrop was "
                "set; pick another layer with Set Backdrop.",
            )
            return {"FINISHED"}
        camera = bpy.context.scene.camera.data  # type: ignore
        assert isinstance(camera, Camera)
        # The file is saved before this, so a /cache that cannot be written costs
        # the artist a backdrop and not the file.
        try:
            backdrop.set_backdrop(camera, shot_root, frames)
        except OSError as error:
            log.exception("Could not build the backdrop proxy for %s.", self.shot_code)
            self.report(
                {"ERROR"},
                f"{created} Its backdrop could not be set because the preview frames "
                f"could not be written under {util.backdrop_root(shot_root)}: "
                f"{error}. Try Set Backdrop once that folder can be written.",
            )
            return {"FINISHED"}
        bpy.ops.wm.save_mainfile()
        self.report(
            {"INFO"},
            f"{created} Backdrop is {backdrop.label(frames)}; change it with "
            "Set Backdrop.",
        )
        return {"FINISHED"}
