"""Reload: bring an fx2d file up to what its Load Layers rows ask for."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import bpy
from bpy.types import (
    ID,
    Camera,
    Collection,
    Context,
    Event,
    Object,
    Operator,
    Scene,
    Timer,
    TransformCacheConstraint,
    ViewLayer,
)
from bpy.app.handlers import persistent
from pxr import Sdf, Usd, UsdGeom

from env_sg import DB_Config
from pipe.core.publish import CURRENT
from pipe.core.shotgrid import ShotGrid, ShotGridError
from pipe.dcc.blender.fx2d import layers, migrate_holdout, util

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)

# The custom property marking what a reload imported, so the next one removes it all,
# materials and world included, and nothing the artist made.
IMPORTED = "skd_fx2d_imported"


class ReloadError(Exception):
    """Why a reload stopped before changing anything, said to the artist."""


def reload(
    scene: Scene,
    view_layer: ViewLayer,
    shot_root: Path,
    chosen: layers.SKD_PG_fx2d_layers,
) -> str:
    """Rebuild the context collection from `chosen`; returns what to tell the artist.

    Adds a row to `chosen` for each set ShotGrid has newly assigned the shot. Raises
    ReloadError, having changed nothing else, when a layer can't be read.
    """
    assigned = _assigned_sets(shot_root.name)
    migrate_holdout.add_rows(shot_root, chosen, assigned or [])
    added = layers.add_missing_rows(chosen, assigned or [])
    missing = layers.missing_pins(shot_root, chosen)
    if missing:
        raise ReloadError(
            f"these pinned versions are gone: {', '.join(missing)}. Pick another "
            "version for each row in Load Layers."
        )
    camera = layers.camera_layer(shot_root, chosen)
    over_camera = layers.camera_layers(shot_root, chosen)
    loaded = layers.loaded_layers(shot_root, chosen)
    sets = layers.set_layers(chosen)
    unreadable = util.unreadable([camera, *over_camera, *sets.values()])
    if unreadable:
        raise ReloadError(
            "these published files are missing or cannot be read: "
            f"{', '.join(unreadable)}. A department may be publishing; try again in "
            "a few minutes."
        )

    # Removed before importing: while the last import holds its layers open, the
    # importer reads them as they were, not as they are on disk now.
    bpy.data.batch_remove(
        [block for block in bpy.data.user_map() if block.get(IMPORTED)]
    )
    migrated = migrate_holdout.remove_holdout(scene, shot_root)
    context_collection = util.child_collection(scene.collection, util.CONTEXT)
    camera_metadata = Sdf.Layer.OpenAsAnonymous(str(camera))
    scene.frame_start = int(camera_metadata.startTimeCode)
    scene.frame_end = int(camera_metadata.endTimeCode)
    _bake_shot_camera(scene, view_layer, context_collection, camera, over_camera)

    stack = util.layers_usd(shot_root)
    layers.write_stack(stack, camera, loaded, sets)
    imported = _import(
        view_layer,
        context_collection,
        stack,
        # The shot camera is the only camera; it is keyed above.
        import_cameras=False,
        **{switch: getattr(chosen, switch) for switch in layers.SWITCHES},
    )
    for block in imported:
        block[IMPORTED] = True
    view_layer.layer_collection.children[util.CONTEXT].holdout = True

    names = [layers.CAMERA, *(layer.stem for layer in reversed(loaded)), *sets]
    message = (
        f"Reloaded {', '.join(names)}: frames {scene.frame_start}-{scene.frame_end}."
    )
    pins = [
        f"{row.name} {row.choice}"
        for row in chosen.rows
        if not row.is_set and row.choice != CURRENT
    ]
    if pins:
        message += f" Pinned: {', '.join(pins)}."
    if added:
        message += f" Added sets newly assigned in ShotGrid: {', '.join(added)}."
    if assigned is None:
        message += (
            " ShotGrid could not be reached, so any set assigned since the last "
            "reload is missing."
        )
    message += migrated
    if any(isinstance(block, Object) and block.type == "MESH" for block in imported):
        message += _turn_off_in_front(scene)
    return message


def _assigned_sets(shot_code: str) -> list[str] | None:
    """The sets ShotGrid assigns the shot; None when it can't be reached."""
    try:
        shot = ShotGrid.connect(DB_Config).get_shot(code=shot_code)
    except ShotGridError:
        # Drawing goes on without ShotGrid: the file's own set rows still load.
        log.exception("Could not ask ShotGrid which sets %s has.", shot_code)
        return None
    return [assigned.name for assigned in shot.sets or []]


def _bake_shot_camera(
    scene: Scene,
    view_layer: ViewLayer,
    context_collection: Collection,
    camera: Path,
    over_camera: list[Path],
) -> None:
    """Key the shot camera on every frame from the camera layer and every row over it.

    Keyed rather than read live, so it changes only on reload. Keyed in place, so
    the backdrop and anything parented to it survive.
    """
    camera_stage = Usd.Stage.Open(str(camera))
    prim_path = next(
        prim.GetPath()
        for prim in camera_stage.Traverse()
        # The stubs take only a Tf.Type, but USD accepts the schema class.
        if prim.IsA(UsdGeom.Camera)  # type: ignore
    )
    shot_camera = _shot_camera(scene, context_collection, prim_path.name)
    with tempfile.TemporaryDirectory() as folder:
        stack = Path(folder) / "camera.usda"
        layers.write_stack(stack, camera, over_camera, {})
        # The importer turns the stage's axes into Blender's, which is all it is
        # trusted with: under the centimetre scale it gets the lens 100 times too long.
        imported = _import(
            view_layer, context_collection, stack, prim_path_mask=str(prim_path)
        )
        # Held for the bake: a prim dies with its stage.
        stage = Usd.Stage.Open(str(stack))
        lens = UsdGeom.Camera(stage.GetPrimAtPath(prim_path))
    importer_camera = next(
        block
        for block in imported
        if isinstance(block, Object) and block.type == "CAMERA"
    )

    data = shot_camera.data
    assert isinstance(data, Camera)
    frame = scene.frame_current
    for key in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(key)
        # The importer's camera carries the centimetre scale; the shot camera's
        # scale stays 1 so nothing parented to it shrinks.
        location, rotation, _ = importer_camera.matrix_world.decompose()
        shot_camera.location = location
        shot_camera.rotation_euler = rotation.to_euler(
            "XYZ", shot_camera.rotation_euler
        )
        shot_camera.keyframe_insert("location", frame=key)
        shot_camera.keyframe_insert("rotation_euler", frame=key)
        # Keyed on every frame so USD, not Blender, interpolates between samples.
        data.lens = lens.GetFocalLengthAttr().Get(key)
        data.keyframe_insert("lens", frame=key)
    bpy.data.batch_remove(imported)

    # Lens and aperture are in millimetres, as published; the clipping range is in
    # the camera's centimetres.
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = lens.GetHorizontalApertureAttr().Get(scene.frame_start)
    data.sensor_height = lens.GetVerticalApertureAttr().Get(scene.frame_start)
    near, far = lens.GetClippingRangeAttr().Get(scene.frame_start)
    data.clip_start = near * layers.CAMERA_SCALE
    data.clip_end = far * layers.CAMERA_SCALE
    # Keying leaves the camera on the last frame until the frame changes.
    scene.frame_set(frame)


def _shot_camera(scene: Scene, context_collection: Collection, name: str) -> Object:
    """The scene camera, emptied of its old move, or a new one for a new file."""
    shot_camera = scene.camera
    if shot_camera is None or shot_camera.name not in context_collection.objects:
        shot_camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        context_collection.objects.link(shot_camera)
        scene.camera = shot_camera
    old: list[ID | None] = []
    # Files made before Load Layers read the move live from cam.usd, through a
    # Transform Cache under a centimetre-scaled parent.
    for constraint in list(shot_camera.constraints):
        if isinstance(constraint, TransformCacheConstraint):
            old.append(constraint.cache_file)
            shot_camera.constraints.remove(constraint)
    shot_camera.parent = None
    shot_camera.rotation_mode = "XYZ"
    shot_camera.scale = (1.0, 1.0, 1.0)
    for animated in (shot_camera, shot_camera.data):
        if animated is not None and animated.animation_data is not None:
            old.append(animated.animation_data.action)
            animated.animation_data_clear()
    bpy.data.batch_remove([block for block in old if block and block.users == 0])
    return shot_camera


def _import(
    view_layer: ViewLayer,
    collection: Collection,
    path: Path,
    # The importer's own options, each typed differently.
    **options: Any,
) -> list[ID]:
    """Import into `collection`; returns every datablock the import made."""
    before = set(bpy.data.user_map())
    active_collection = view_layer.active_layer_collection
    # The importer selects what it brings in and makes it the active object, which
    # drops an artist who is drawing out of Draw mode.
    active_object = view_layer.objects.active
    selected = [obj for obj in view_layer.objects if obj.select_get()]
    # Imported objects land in the active collection.
    view_layer.active_layer_collection = view_layer.layer_collection.children[
        collection.name
    ]
    try:
        # The camera layer sets the frame range, not whatever the stack's says.
        bpy.ops.wm.usd_import(filepath=str(path), set_frame_range=False, **options)
    finally:
        # Left on the context collection, the artist's next strokes would land
        # where they are never delivered and are wiped by the next reload.
        view_layer.active_layer_collection = active_collection
        for obj in view_layer.objects:
            obj.select_set(obj in selected)
        view_layer.objects.active = active_object
    return [block for block in bpy.data.user_map() if block not in before]


def _turn_off_in_front(scene: Scene) -> str:
    # Grease Pencil objects from the Add menu start with In Front on, which draws
    # them over the holdout no matter where they sit in depth.
    in_front = [
        obj for obj in scene.objects if obj.type == "GREASEPENCIL" and obj.show_in_front
    ]
    for obj in in_front:
        obj.show_in_front = False
    if not in_front:
        return ""
    return f" Turned off In Front on: {', '.join(obj.name for obj in in_front)}."


class SKD_OT_fx2d_reload(Operator):
    """Bring the file up to the latest publishes Load Layers asks for.

    Re-keys the shot camera, resets the frame range and replaces what the last
    reload imported. Drawings, layers and the backdrop are kept.
    """

    bl_idname = "skd.fx2d_reload"
    bl_label = "Reload"

    @classmethod
    def poll(cls, context: Context) -> bool:
        return util.poll_fx2d_file(cls)

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        return _reload_and_report(self, context, save=False)


class SKD_OT_fx2d_reload_on_open(Operator):
    """Reload the file that has just opened, then save it."""

    bl_idname = "skd.fx2d_reload_on_open"
    bl_label = "Reload on Open"
    bl_options = {"INTERNAL"}

    _timer: Timer | None = None

    @classmethod
    def poll(cls, context: Context) -> bool:
        return util.poll_fx2d_file(cls)

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        # Reloads from `modal`: only a report made there reaches the status bar. One
        # made by an operator Python called reaches only the console.
        window_manager = context.window_manager
        assert window_manager is not None
        self._timer = window_manager.event_timer_add(0.0, window=context.window)
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        window_manager = context.window_manager
        assert window_manager is not None and self._timer is not None
        window_manager.event_timer_remove(self._timer)
        # Left unsaved, every file would open with changes, and Open Shot refuses to
        # leave a file that has them.
        return _reload_and_report(self, context, save=True)


def _reload_and_report(
    operator: Operator, context: Context, save: bool
) -> set[OperatorReturnItems]:
    shot_root = util.shot_root()
    scene, view_layer = context.scene, context.view_layer
    assert shot_root is not None and scene is not None and view_layer is not None
    try:
        message = reload(scene, view_layer, shot_root, layers.scene_layers(scene))
    except ReloadError as error:
        operator.report(
            {"ERROR"}, f"Could not reload because {error} Nothing was changed."
        )
        return {"CANCELLED"}
    if save:
        bpy.ops.wm.save_mainfile()
    operator.report({"INFO"}, message)
    return {"FINISHED"}


@persistent
def reload_on_open(_path: str) -> None:
    """A `load_post` handler: an fx2d file reloads as it opens."""
    # A script opening the file in --background gets it as last saved: there are no
    # events to run the reload, and no status bar to report to.
    if util.shot_root() is None or bpy.app.background:
        return
    window_manager = bpy.context.window_manager
    assert window_manager is not None
    # Opening a file from inside an operator, as Open Shot does, leaves the context
    # with no window, and a modal operator needs one.
    with bpy.context.temp_override(window=window_manager.windows[0]):
        # The stubs list only Blender's own operators.
        bpy.ops.skd.fx2d_reload_on_open("INVOKE_DEFAULT")  # type: ignore
