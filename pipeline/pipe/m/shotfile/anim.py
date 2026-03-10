import logging
from pathlib import Path
from typing import Optional

import maya.cmds as mc
from pxr import Usd, UsdGeom
from shared.util import get_production_path

from pipe.glui.dialogs import MessageDialog
from pipe.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from pipe.glui.version_browser import VersionBrowserWidget
from pipe.shot.version_adapter import (
    maya_anim_stream,
    path_matches_stream,
    shot_owner_for,
)
from pipe.struct.db import Shot
from pipe.versioning import (
    VersionStreamSpec,
    list_version_records,
    promote_version,
    save_version,
    version_label,
)

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
            mc.mayaUsdDiscardEdits(CAM_NAME)
        except RuntimeError:
            pass
        finally:
            camera_prim = next(
                prim
                for prim in cls.get_stage().Traverse(Usd.PrimIsDefined)
                if prim.IsA(UsdGeom.Camera) and prim.GetName() == CAM_NAME
            )
            mc.mayaUsdEditAsMaya(
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
                    mc.warning("Could not locate USD shot camera in Maya scene.")

    def _get_subpath(self) -> str:
        return "anim"

    def _setup_scene(self) -> None:
        self._import_camera()

        # Import Rigs
        for asset_stub in self.shot.assets:
            asset = self._conn.get_asset_by_stub(asset_stub)
            if not asset.asset_path:
                continue
            rig_path = "/".join(("anim", "Rigs", asset.name + ".mb"))
            print(str(get_production_path()) + "/../" + rig_path)
            if (get_production_path() / ".." / rig_path).exists():
                mc.file(rig_path, reference=True, namespace=asset.name)
            else:
                rig_path = "/".join(("anim", "Rigs", asset.name.capitalize() + ".mb"))
                print(str(get_production_path()) + "/../" + rig_path)
                if (get_production_path() / ".." / rig_path).exists():
                    mc.file(rig_path, reference=True, namespace=asset.name)
                else:
                    print(f'Unable to find rig for asset "{asset.display_name}"')

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

    def open_version_browser(self) -> None:
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                "No valid animation shot file is open. Use Open Anim first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_anim_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current scene to a valid animation shot file. Use Open Anim first.",
                "Version History",
            ).exec_()
            return

        shot, anim_stream = resolved
        records = list_version_records(anim_stream)
        if not records:
            MessageDialog(
                self._main_window,
                "No version history was found for this shot.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            owner_label=shot.code,
            stream_label=anim_stream.label,
        )
        if not browser.exec_():
            return

        selected_record = browser.get_selected_record()
        selected_action = browser.get_selected_action()
        if selected_record is None:
            return

        if selected_action == VersionBrowserWidget.ACTION_OPEN:
            backup_path = selected_record.backup_path
            if backup_path is None:
                MessageDialog(
                    self._main_window,
                    "The selected version has no backup file path.",
                    "Open Version Failed",
                ).exec_()
                return
            if not backup_path.exists() or not backup_path.is_file():
                MessageDialog(
                    self._main_window,
                    f"Backup file is missing on disk:\n{backup_path}",
                    "Open Version Failed",
                ).exec_()
                return
            if not self._check_unsaved_changes():
                return

            try:
                self._open_file(backup_path)
                self._post_open_file(shot)
            except Exception as exc:
                log.exception(
                    "Failed to open animation backup version: %s", backup_path
                )
                MessageDialog(
                    self._main_window,
                    f"Failed to open selected version:\n{exc}",
                    "Open Version Failed",
                ).exec_()
            return

        if selected_action == VersionBrowserWidget.ACTION_PROMOTE:
            source_backup = selected_record.backup_path
            if source_backup is None or not source_backup.exists():
                MessageDialog(
                    self._main_window,
                    "Cannot create a new version from this entry because the backup file is missing.",
                    "Create Version Failed",
                ).exec_()
                return

            promote_dialog = PromoteVersionDialog(self._main_window, selected_record)
            if not promote_dialog.exec_():
                return

            try:
                promoted_record = promote_version(
                    selected_record,
                    anim_stream,
                    title=promote_dialog.get_title(),
                    note=promote_dialog.get_note(),
                )
            except Exception as exc:
                log.exception("Failed to create a new animation version.")
                MessageDialog(
                    self._main_window,
                    f"Failed to create new version:\n{exc}",
                    "Create Version Failed",
                ).exec_()
                return

            MessageDialog(
                self._main_window,
                (
                    f'Created new version {version_label(promoted_record.version)} '
                    f'"{promoted_record.title or "(untitled)"}" from the selected backup.\n'
                    "Open it from Version History to continue working from it."
                ),
                "Version Created",
            ).exec_()

    def save_version_for_current_scene(self) -> None:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return

        resolved = self._resolve_current_anim_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current scene to a valid animation shot file.",
                "Shot Not Resolved",
            ).exec_()
            return

        _shot, anim_stream = resolved
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return

        try:
            version_record = save_version(
                scene_path,
                anim_stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save manual animation version.")
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            (
                f'Saved {version_label(version_record.version)} '
                f'"{version_record.title or "(untitled)"}".'
            ),
            "Version Saved",
        ).exec_()
