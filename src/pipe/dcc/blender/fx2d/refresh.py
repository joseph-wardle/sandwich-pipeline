from __future__ import annotations

from typing import TYPE_CHECKING

from bpy.types import (
    Camera,
    Collection,
    Context,
    Operator,
    Scene,
    TransformCacheConstraint,
)
from pxr import Usd, UsdGeom

from pipe.dcc.blender.fx2d import util
from pipe.dcc.blender.fx2d.holdout import rebuild_holdout, source_layers

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems


def _reread_camera_move(context_collection: Collection) -> None:
    for obj in context_collection.objects:
        for constraint in obj.constraints:
            if (
                isinstance(constraint, TransformCacheConstraint)
                and constraint.cache_file is not None
            ):
                # The camera move is read live from cam.usd, but only when the
                # file is opened. Assigning the path makes Blender read it again.
                constraint.cache_file.filepath = constraint.cache_file.filepath


def _rekey_lens(scene: Scene, camera: Camera, stage: Usd.Stage) -> None:
    """Key the lens from the stage on every frame of the scene range."""
    focal_length = next(
        UsdGeom.Camera(prim).GetFocalLengthAttr()
        for prim in stage.Traverse()
        if prim.IsA(UsdGeom.Camera)  # type: ignore
    )
    # The importer bakes the lens to keyframes, which go stale. Re-keyed in
    # place because a new camera would drop the backdrop. Keyed on every frame
    # so USD, not Blender, interpolates between the published samples. The value
    # is taken as is because published cameras are in centimetres; the importer
    # would scale any other unit.
    for frame in range(scene.frame_start, scene.frame_end + 1):
        camera.lens = focal_length.Get(frame)
        camera.keyframe_insert("lens", frame=frame)
    # Keying leaves the lens on the last frame's value until the frame changes.
    scene.frame_set(scene.frame_current)


class SKD_OT_fx2d_refresh(Operator):
    """Pick up a republished camera and animation.

    Re-reads the camera move, re-keys the lens, resets the scene frame range, and
    rebuilds the holdout if the file has one.
    """

    bl_idname = "skd.fx2d_refresh"
    bl_label = "Refresh"

    @classmethod
    def poll(cls, context: Context) -> bool:
        return util.poll_fx2d_file(cls)

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene, view_layer = context.scene, context.view_layer
        assert shot_root is not None and scene is not None and view_layer is not None

        context_collection = scene.collection.children[util.CONTEXT]
        camera_usd = util.camera_usd(shot_root)
        holdout_layers = [
            layer
            for source in util.HOLDOUT_SOURCES
            for layer in source_layers(shot_root, source)
        ]
        unreadable = util.unreadable((camera_usd, *holdout_layers))
        if unreadable:
            self.report(
                {"ERROR"},
                "Could not refresh because these published files are missing or "
                f"cannot be read: {', '.join(unreadable)}. A department may be "
                "publishing; try again in a few minutes. Nothing was changed.",
            )
            return {"CANCELLED"}

        stage = Usd.Stage.Open(str(camera_usd))
        stage.Reload()
        _reread_camera_move(context_collection)
        scene.frame_start = int(stage.GetStartTimeCode())
        scene.frame_end = int(stage.GetEndTimeCode())
        camera = next(
            obj.data for obj in context_collection.objects if obj.type == "CAMERA"
        )
        assert isinstance(camera, Camera)
        _rekey_lens(scene, camera, stage)

        message = f"Refreshed the camera: frames {scene.frame_start}-{scene.frame_end}."
        if holdout_layers:
            message += f" {rebuild_holdout(scene, view_layer, shot_root)}"
        self.report({"INFO"}, message)
        return {"FINISHED"}
