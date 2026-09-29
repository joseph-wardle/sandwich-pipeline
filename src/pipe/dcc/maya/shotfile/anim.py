import logging
from pathlib import Path
from typing import Any, cast

import maya.cmds as mc
from pxr import Sdf, Usd, UsdGeom

from pipe.dcc.maya.util.camera import apply_gate_mask
from pipe.dcc.maya.rig.utils import get_rig_filepath_from_asset
from pipe.core.shot import maya_anim_stream, shot_owner_for
from pipe.core.shotgrid import (
    SGEntity,
    Shot,
    build_shot_path,
    is_previs_shot_code,
)
from pipe.core.ui import MessageDialog
from pipe.dcc.maya.runtime import get_main_qt_window
from pipe.core.versioning import VersionStreamSpec, path_matches_stream

from .shotfile_manager import MShotFileManager
from .sets import sync_shot_sets
from .stage import add_sublayer, get_stage, get_stage_shape

log = logging.getLogger(__name__)

SHOT_CAMERA_NAME = "shotCam"


def _find_camera_prim(stage: Usd.Stage) -> Usd.Prim | None:
    return next(
        (
            prim
            for prim in stage.Traverse(Usd.PrimIsDefined)
            # `cast`: ty does not see `IsA` on the prims `Traverse` yields.
            if cast(Any, prim).IsA(UsdGeom.Camera)
            and prim.GetName() == SHOT_CAMERA_NAME
        ),
        None,
    )


def _sublayer_camera(stage: Usd.Stage, shot_path: str) -> bool:
    """Sublayer the shot's published camera into `stage`. False if none is published."""
    # Production-root-relative, resolved by `PXR_AR_DEFAULT_SEARCH_PATH`.
    cam_layer = Sdf.Layer.FindOrOpen("/".join((shot_path, "cam", "cam.usd")))
    if not cam_layer:
        return False
    add_sublayer(stage.GetRootLayer(), cam_layer)
    return True


def _report_missing_camera() -> None:
    message = (
        "No shot camera was loaded because this shot has no published camera.\n\n"
        "Publish the camera from the shot's RLO scene, then reopen this scene."
    )
    mc.warning(message.replace("\n\n", " "))
    if not mc.about(batch=True):
        MessageDialog(get_main_qt_window(), message, "Shot Camera").exec_()


def _find_usd_shotcam() -> str | None:
    """Locate the shot camera brought in via MayaUSD"""
    # look for any transform named shotCam under the mayaUsd proxy (__mayaUsd__ in its path)
    candidates = [
        path
        for path in (mc.ls("*shotCam", type="transform", long=True) or [])
        if "__mayaUsd__" in path.split("|")
    ]
    if not candidates:
        return None
    # prefer shortest (legacy: |__mayaUsd__|shotCamParent|shotCam), otherwise deterministic
    candidates.sort(key=len)
    return candidates[0]


def _lock_camera_chain(cam_transform: str) -> None:
    """Lock transforms on the shot camera and every parent to prevent accidental edits."""
    parts = cam_transform.split("|")
    current = ""
    for part in parts[1:]:  # skip leading empty string
        current = f"{current}|{part}"
        if not mc.objExists(current):
            continue
        for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            try:
                mc.setAttr(
                    f"{current}.{attr}", lock=True, keyable=False, channelBox=False
                )
            except Exception:
                pass
    for shape in mc.listRelatives(cam_transform, shapes=True, fullPath=True) or []:
        try:
            mc.camera(shape, edit=True, lockTransform=True)
        except Exception:
            pass


class MAnimShotFileManager(MShotFileManager):
    @classmethod
    def run_on_open(cls):
        super().run_on_open()

        stage = get_stage()
        camera_prim = _find_camera_prim(stage)
        if camera_prim is None:
            shot_code = cls._shot_code_from_file_info()
            if shot_code and _sublayer_camera(stage, build_shot_path(shot_code)):
                camera_prim = _find_camera_prim(stage)
        if camera_prim is None:
            _report_missing_camera()
            return

        try:
            mc.mayaUsdDiscardEdits(SHOT_CAMERA_NAME)  # type: ignore
        except RuntimeError:
            pass
        mc.mayaUsdEditAsMaya(  # type: ignore
            get_stage_shape() + "," + str(camera_prim.GetPrimPath())
        )
        cam_path = _find_usd_shotcam()
        if cam_path:
            _lock_camera_chain(cam_path)
            for shape in mc.listRelatives(cam_path, shapes=True, fullPath=True) or []:
                apply_gate_mask(shape)
            mc.lookThru(cam_path)
        else:
            # fallback to legacy name if discovery fails
            try:
                camera_shape = mc.listRelatives(
                    SHOT_CAMERA_NAME, fullPath=True, shapes=True
                )[0]
                mc.camera(camera_shape, edit=True, lockTransform=True)
                mc.lookThru(SHOT_CAMERA_NAME)
            except Exception:
                log.warning("Could not locate USD shot camera in Maya scene.")

    def _get_subpath(self) -> str:
        return "anim"

    def _filter_entities(self, entities: list[SGEntity]) -> list[SGEntity]:
        # Previs sequence proxies aren't real shots; animators never open them.
        return [e for e in entities if not is_previs_shot_code(e.code)]

    def _setup_scene(self) -> None:
        if not _sublayer_camera(get_stage(), self.shot.shot_path):
            mc.warning("No exported camera found")

        # Import Rigs. ``self.shot.assets`` carries partial Assets (id + code
        # only); accessing ``asset.is_rigged`` lazy-fetches the full record.
        for asset in self.shot.assets or []:
            if not asset.is_rigged:
                continue
            rig_path = get_rig_filepath_from_asset(asset)

            if rig_path.exists():
                mc.file(rig_path, reference=True, namespace=asset.name)  # type: ignore
            else:
                log.warning(
                    f"Couldn't find the rig file for {asset.display_name} even though it's tagged as rigged"
                )

        sync_shot_sets(self.shot)

    def _setup_file(self, path: Path, entity) -> None:
        mc.file(newFile=True, force=True)
        super()._setup_file(path, entity)

    def _resolve_current_anim_stream(
        self,
        scene_path: Path,
    ) -> tuple[Shot, VersionStreamSpec] | None:
        shot = self._resolve_shot_for_scene(scene_path)
        if shot is None:
            return None

        stream = maya_anim_stream(shot, owner=shot_owner_for(shot))
        if not path_matches_stream(scene_path, stream):
            return None
        return shot, stream

    def _entity_label(self) -> str:
        return "animation"

    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        result = self._resolve_current_anim_stream(scene_path)
        if result is None:
            return None
        shot, stream = result
        return stream, shot.code or "", shot
