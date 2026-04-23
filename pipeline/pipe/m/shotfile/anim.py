import logging
from pathlib import Path
from typing import Any, Optional, cast

import maya.cmds as mc
from pxr import Usd, UsdGeom

from pipe.m.rig.utils import get_rig_filepath_from_asset
from pipe.shot.version_adapter import (
    maya_anim_stream,
    shot_owner_for,
)
from pipe.struct.db import Shot
from pipe.versioning import VersionStreamSpec, path_matches_stream

from .shotfile_manager import MShotFileManager

log = logging.getLogger(__name__)


def _find_usd_shotcam() -> Optional[str]:
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

        # Duplicate the USD camera into a temp Maya camera
        CAM_NAME = "shotCam"
        try:
            mc.mayaUsdDiscardEdits(CAM_NAME)  # type: ignore
        except RuntimeError:
            pass
        finally:
            camera_prim = next(
                prim
                for prim in cls.get_stage().Traverse(Usd.PrimIsDefined)
                if cast(Any, prim).IsA(UsdGeom.Camera) and prim.GetName() == CAM_NAME
            )
            mc.mayaUsdEditAsMaya(  # type: ignore
                cls.get_stage_shape() + "," + str(camera_prim.GetPrimPath())
            )
            cam_path = _find_usd_shotcam()
            if cam_path:
                _lock_camera_chain(cam_path)
                mc.lookThru(cam_path)
            else:
                # fallback to legacy name if discovery fails
                try:
                    camera_shape = mc.listRelatives(
                        CAM_NAME, fullPath=True, shapes=True
                    )[0]
                    mc.camera(camera_shape, edit=True, lockTransform=True)
                    mc.lookThru(CAM_NAME)
                except Exception:
                    log.warning("Could not locate USD shot camera in Maya scene.")

    def _get_subpath(self) -> str:
        return "anim"

    def _setup_scene(self) -> None:
        self._import_camera()

        # Import Rigs
        for asset_stub in self.shot.assets:
            asset = self._conn.get_asset_by_stub(asset_stub)
            if not asset.is_rigged:
                continue
            rig_path = get_rig_filepath_from_asset(asset)

            if rig_path.exists():
                mc.file(rig_path, reference=True, namespace=asset.name)  # type: ignore
            else:
                log.warning(
                    f"Couldn't find the rig file for {asset.display_name} even though it's tagged as rigged"
                )

        self._import_env()

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
        return stream, shot.code, shot
