from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import bpy
from bpy.types import Camera, Context, Event, Operator, Window
from pxr import Usd

from env_sg import DB_Config
from pipe.core.shot import current_layer_path, shot_root_path
from pipe.core.shotgrid import ShotGrid, is_previs_shot_code
from pipe.dcc.blender.fx2d import backdrop, layers, util
from pipe.dcc.blender.fx2d.reload import ReloadError, reload

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)

# Blender keeps pointers into the strings an enum callback returns, so the list has
# to outlive the call or the search popup shows garbage.
_SHOT_ITEMS: list[tuple[str, str, str]] = []


def _shot_items(self: Operator, context: Context | None) -> list[tuple[str, str, str]]:
    conn = ShotGrid.connect(DB_Config)
    codes = sorted(
        shot.code
        for shot in conn.find_shots()
        # A previs sequence is a Shot in ShotGrid but has no camera or renders.
        if shot.code and not is_previs_shot_code(shot.code)
    )
    _SHOT_ITEMS[:] = [(code, code, "") for code in codes]
    return _SHOT_ITEMS


def _create(shot_root: Path, path: Path) -> None:
    """Raises ReloadError, leaving an unsaved empty file, when a layer can't be read."""
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    assert scene is not None and view_layer is not None

    # Made first so it sits above the layers in the outliner.
    util.child_collection(scene.collection, util.CONTEXT)
    layer = util.child_collection(scene.collection, util.DEFAULT_LAYER)
    layer_collections = view_layer.layer_collection.children
    # Loading a file from inside an operator leaves the context with no window,
    # and the importer refuses to run without one.
    window = bpy.context.window_manager.windows[0]  # type: ignore
    with bpy.context.temp_override(window=window):
        reload(scene, view_layer, shot_root, layers.scene_layers(scene))
    # A new scene sits on frame 1, outside the shot, where a backdrop has no frame.
    scene.frame_current = scene.frame_start
    # Reload sets the frame range from the camera layer but not the fps.
    camera_usd = current_layer_path(shot_root, layers.CAMERA)
    scene.render.fps = round(Usd.Stage.Open(str(camera_usd)).GetTimeCodesPerSecond())
    util.apply_render_settings(scene, view_layer)

    # Anything drawn outside a layer collection would render into every layer.
    view_layer.active_layer_collection = layer_collections[layer.name]
    _add_drawing(window)

    # The backdrop only shows through the shot camera; onion skins are off by default.
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            space.region_3d.view_perspective = "CAMERA"  # type: ignore
            space.overlay.use_gpencil_onion_skin = True  # type: ignore
    _frame_timeline(window)

    path.parent.mkdir(parents=True, exist_ok=True)
    # Save As rewrites file paths relative to the .blend by default, which turns
    # production paths into `../` chains that break if the file moves.
    bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=False)


def _add_drawing(window: Window) -> None:
    """A blank Grease Pencil object keyed on the current frame, in Draw mode."""
    with bpy.context.temp_override(window=window):
        bpy.ops.object.grease_pencil_add(type="EMPTY")
        drawing = bpy.context.object
        assert drawing is not None
        # Lit strokes change colour with the shot's lights, and In Front draws them
        # over the holdout.
        drawing.use_grease_pencil_lights = False
        drawing.show_in_front = False
        bpy.ops.object.mode_set(mode="PAINT_GREASE_PENCIL")


def _frame_timeline(window: Window) -> None:
    # Frame All would frame the drawing's one key, not the shot.
    for area in window.screen.areas:
        if area.ui_type == "TIMELINE":
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.anim.scene_range_frame()


class SKD_OT_fx2d_open_shot(Operator):
    """Open a shot's fx2d file, creating it from the shot camera the first time."""

    bl_idname = "skd.fx2d_open_shot"
    bl_label = "Open Shot"
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
            util.link_cache_dirs(shot_root)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            return {"FINISHED"}

        camera_usd = current_layer_path(shot_root, layers.CAMERA)
        if not camera_usd.exists():
            self.report(
                {"ERROR"},
                f"Could not create the fx2d file because {self.shot_code} has no "
                f"published camera at {camera_usd}. Ask layout to publish the camera.",
            )
            return {"CANCELLED"}
        try:
            _create(shot_root, path)
        except ReloadError as error:
            self.report({"ERROR"}, f"Could not create the fx2d file because {error}")
            return {"CANCELLED"}
        util.link_cache_dirs(shot_root)
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
        # The file is saved before this, so a backdrop folder that cannot be written
        # costs the artist a backdrop and not the file.
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
