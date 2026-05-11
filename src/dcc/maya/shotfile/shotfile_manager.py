from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Iterable, cast

import maya.api.OpenMaya as om
import maya.cmds as mc
import mayaUsd  # type: ignore[import-not-found]
from env_sg import DB_Config
from pxr import Sdf, Usd, UsdGeom
from core.util.util import get_production_path
from timeline_marker.ui import TimelineMarker  # type: ignore[import-not-found]

from core.glui.dialogs import MessageDialog
from core.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from core.glui.version_browser import VersionBrowserWidget
from dcc.maya.runtime import get_main_qt_window
from core.shotgrid import (
    SGEntity,
    Shot,
    ShotGrid,
    build_shot_path,
    validate_shot_code_token,
)
from core.util import FileManager, log_errors
from core.versioning import (
    VersionStreamSpec,
    list_version_records,
    version_label,
)
from core.versioning import (
    promote_version as _promote_version,
)
from core.versioning import (
    save_version as _save_version,
)

from .timeline import shot_timeline_generator

log = logging.getLogger(__name__)


class MShotFileManager(FileManager):
    MAYA_OVERRIDE = "maya_override.usd"
    shot: Shot

    def __init__(self, **kwargs) -> None:
        conn = ShotGrid.connect(DB_Config)
        window = get_main_qt_window()
        super().__init__(conn, Shot, window, versioning=True, **kwargs)

    @classmethod
    def get_stage_shape(cls) -> str:
        if ss := mc.ls(type="mayaUsdProxyShape", long=True)[0]:
            return ss
        raise RuntimeError("No USD stage found in scene")

    @classmethod
    def get_stage(cls) -> Usd.Stage:
        return mayaUsd.ufe.getStage(cls.get_stage_shape())

    @classmethod
    def _normalize_usd_path(cls, path: str) -> str:
        return path.replace("\\", "/")

    @classmethod
    def _shot_code_from_file_info(cls) -> str | None:
        info = mc.fileInfo("code", query=True)
        if isinstance(info, (list, tuple)):
            if not info:
                return None
            raw_value = info[0]
        elif isinstance(info, str):
            raw_value = info
        else:
            return None

        try:
            return validate_shot_code_token(raw_value)
        except ValueError:
            log.warning("Invalid shot code in scene metadata: %s", raw_value)
            return None

    @classmethod
    def _shot_code_from_scene_path(cls, scene_path: str | None) -> str | None:
        """Resolve shot code from a scene path using canonical shot folder semantics.

        Preferred source is the directory token immediately after `shot/`.
        Falls back to the scene filename stem for legacy/non-canonical paths.
        """
        if not scene_path:
            return None
        path = Path(scene_path)
        try:
            shot_index = path.parts.index("shot")
            if shot_index + 1 < len(path.parts):
                try:
                    return validate_shot_code_token(path.parts[shot_index + 1])
                except ValueError:
                    log.warning("Invalid shot token in scene path: %s", scene_path)
        except ValueError:
            pass
        stem = path.stem
        if not stem:
            return None
        try:
            return validate_shot_code_token(stem.split(".")[0])
        except ValueError:
            return None

    @classmethod
    def _edit_target_path_for_shot(cls, shot_code: str) -> str:
        """Return canonical edit target path for a shot override layer."""
        return "/".join((build_shot_path(shot_code), "set", cls.MAYA_OVERRIDE))

    @classmethod
    def _ensure_sublayer(
        cls,
        root_layer: Sdf.Layer,
        layer_path: str,
        *,
        label: str,
        insert_after: str | None = None,
    ) -> Sdf.Layer | None:
        layer = Sdf.Layer.FindOrOpenRelativeToLayer(root_layer, layer_path)
        if not layer:
            log.warning("Could not open %s layer at %s", label, layer_path)
            return None

        identifier = layer.identifier
        sublayers = list(cast(Iterable[str], root_layer.subLayerPaths))

        if identifier in sublayers:
            if insert_after and insert_after in sublayers:
                current_index = sublayers.index(identifier)
                desired_index = sublayers.index(insert_after) + 1
                if current_index != desired_index:
                    sublayers.pop(current_index)
                    if desired_index > current_index:
                        desired_index -= 1
                    sublayers.insert(desired_index, identifier)
        else:
            if insert_after and insert_after in sublayers:
                sublayers.insert(sublayers.index(insert_after) + 1, identifier)
            else:
                sublayers.append(identifier)

        root_layer.subLayerPaths[:] = sublayers
        return layer

    @classmethod
    def _find_root_layer_path(cls, scene_path: Path) -> Path | None:
        for parent in scene_path.parents:
            candidate = parent / "maya_root.usd"
            if candidate.exists():
                return candidate
        return None

    @classmethod
    @log_errors
    def run_on_open(cls) -> None:
        """Function to run on file open via script node"""

        # save edit target layer on save
        beforeSaveId = om.MSceneMessage.addCallback(
            om.MSceneMessage.kBeforeSave,
            lambda _: MShotFileManager.get_stage().GetEditTarget().GetLayer().Save(),
        )

        # remove callback before opening a new file
        om.MSceneMessage.addCallback(
            om.MSceneMessage.kBeforeOpen,
            lambda kwargs: om.MSceneMessage.removeCallback(kwargs["ID"]),
            {"ID": beforeSaveId},
        )

        # change default render resolution
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore

        # set session USD target layer to the override layer
        try:
            shot_code = cls._shot_code_from_file_info()
            if not shot_code:
                scene_path = mc.file(query=True, sceneName=True)
                scene_path_str = scene_path if isinstance(scene_path, str) else ""
                shot_code = cls._shot_code_from_scene_path(scene_path_str)
                if shot_code:
                    mc.fileInfo("code", shot_code)
                else:
                    mc.warning("Could not determine shot code; USD edit target not set")
                    return
            assert shot_code is not None
            mc.mayaUsdEditTarget(  # type: ignore
                cls.get_stage_shape(),
                edit=True,
                editTarget=cls._edit_target_path_for_shot(shot_code),
            )

            conn = ShotGrid.connect(DB_Config)
            shot = conn.get_shot(code=shot_code)

            # Import Timeline
            frames, colors, comments = shot_timeline_generator(shot.cut_duration or 0)
            TimelineMarker.clear()
            TimelineMarker.set(frames, colors, comments)
            mc.playbackOptions(
                animationStartTime=frames[0],
                animationEndTime=frames[-1],
                minTime=frames[0],
                maxTime=frames[-1],
            )
        except Exception:
            # Workflow boundary: many things can fail during file-open setup
            # (ShotGrid lookup, USD edit target, timeline marker). Log + warn
            # rather than crash the open.
            log.exception("run_on_open failed")
            mc.error(
                "Could not finish file-open setup. Check the script editor for details."
            )

    def _check_unsaved_changes(self) -> bool:
        if mc.file(query=True, modified=True):
            warning_response = mc.confirmDialog(
                title="Do you want to save?",
                message="The current file has not been saved. Continue anyways?",
                button=["Continue", "Cancel"],
                defaultButton="Cancel",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if warning_response == "Cancel":
                return False
        return True

    def _current_scene_path(self) -> Path | None:
        scene_raw = mc.file(query=True, sceneName=True)
        if not isinstance(scene_raw, str) or not scene_raw:
            return None
        return Path(scene_raw).expanduser().resolve()

    def _ensure_scene_saved(self) -> Path | None:
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                "Scene must be saved before creating a version.",
                "Save Required",
            ).exec_()
            return None

        if mc.file(query=True, modified=True):
            response = mc.confirmDialog(
                title="Save Changes",
                message="This scene has unsaved changes. Save before creating a version?",
                button=["Save", "Cancel"],
                defaultButton="Save",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if response != "Save":
                return None
            try:
                mc.file(save=True, force=True)
            except Exception:
                MessageDialog(
                    self._main_window,
                    "Failed to save the current scene. Resolve any file issues and try again.",
                    "Save Failed",
                ).exec_()
                log.exception("Failed to save Maya shot scene before creating version.")
                return None
            scene_path = self._current_scene_path()
            if scene_path is None:
                MessageDialog(
                    self._main_window,
                    "Could not resolve the current scene path after save.",
                    "Save Failed",
                ).exec_()
                return None

        return scene_path

    def _resolve_shot_for_scene(self, scene_path: Path) -> Shot | None:
        shot_code = self._shot_code_from_file_info() or self._shot_code_from_scene_path(
            str(scene_path)
        )
        if not shot_code:
            return None

        shot = self._conn.get_shot(code=shot_code)
        if shot.code:
            mc.fileInfo("code", shot.code)
        return shot

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        shot = cast(Shot, entity)
        return shot.code or "", "mb"

    def _open_file(self, path: Path) -> None:
        mc.file(str(path), open=True, force=True)

    def _post_open_file(self, entity: SGEntity) -> None:
        """create `lndOnOpen` script node"""
        ON_OPEN_SCRIPT = "lndOnOpen"

        if mc.objExists(ON_OPEN_SCRIPT):
            return

        classname = self.__class__.__name__
        mc.scriptNode(
            beforeScript=(
                f"from dcc.maya.shotfile import {classname};"
                f"{classname}.{self.__class__.run_on_open.__name__}()"
            ),
            name=ON_OPEN_SCRIPT,
            scriptType=1,
            sourceType="python",
        )
        # script node is created, will not run this session, so run manually
        self.run_on_open()

    def _import_camera(self) -> None:
        shot_path = self.shot.shot_path
        root_layer = self.get_stage().GetRootLayer()

        # mc.mayaUsdLayerEditor(cam_layer.identifier, edit=True, lockLayer=(2, 0, stageShape))

        cam_file_layer = Sdf.Layer.FindOrOpenRelativeToLayer(
            root_layer, "/".join((shot_path, "cam", "cam.usd"))
        )
        if not cam_file_layer:
            mc.warning("No exported camera found")
            return

        if cam_file_layer.identifier not in root_layer.subLayerPaths:  # type: ignore[operator]
            root_layer.subLayerPaths.append(cam_file_layer.identifier)

    def _import_env(self) -> None:
        shot_path = self.shot.shot_path
        stage = self.get_stage()
        root_layer = stage.GetRootLayer()
        # locked_layers: list[str] = []

        ## Fix env scale
        stage.SetEditTarget(Usd.EditTarget(root_layer))
        env_prim = stage.OverridePrim(Sdf.Path("/environment"))
        env_xformable = UsdGeom.Xformable(env_prim)
        env_xformable.ClearXformOpOrder()
        env_scale_op = env_xformable.AddScaleOp()
        env_scale_op.Set((100, 100, 100))

        # Set up shot-level overrides
        env_override_layer = Sdf.Layer.FindOrOpenRelativeToLayer(
            root_layer,
            "/".join((shot_path, "set", MShotFileManager.MAYA_OVERRIDE)),
        ) or Sdf.Layer.CreateNew(
            str(
                get_production_path()
                / shot_path
                / "set"
                / MShotFileManager.MAYA_OVERRIDE
            )
        )
        if not env_override_layer:
            log.warning("Unable to create or open shot override layer.")
        else:
            env_override_layer.Save()

            if env_override_layer.identifier not in root_layer.subLayerPaths:  # type: ignore[operator]
                root_layer.subLayerPaths.append(env_override_layer.identifier)

            stage.SetEditTarget(Usd.EditTarget(env_override_layer))

        # Linked Environment refs from `shot.sets` / `shot.set` / `shot.sequence.set`
        # arrive partial; accessing `.environment_path` triggers lazy-fetch.
        envs = self.shot.sets
        if not envs:
            sequence = self.shot.sequence
            sole_env = self.shot.set or (sequence.set if sequence else None)
            envs = [sole_env] if sole_env else []

        for env in envs:
            if env is None:
                continue
            env_path = env.environment_path
            env_file_layer = self._ensure_sublayer(
                root_layer,
                env_path,
                label=f"environment layout ({env_path})",
            )
            if env_file_layer:
                env_file_layer.SetPermissionToSave(False)

        # for id in locked_layers:
        #     mc.mayaUsdLayerEditor(id, edit=True, lockLayer=(2, 0, stageShape))

    @abstractmethod
    def _setup_scene(self) -> None:
        pass

    def _setup_file(self, path: Path, entity) -> None:
        mc.file(rename=str(path))

        self.shot = cast(Shot, entity)
        shot_path = self.shot.shot_path

        # Create USD Stage
        transform = mc.createNode("transform", name="stage_transform")
        mc.createNode("mayaUsdProxyShape", name="stage", parent=transform)
        stage_shape = self.get_stage_shape()
        mc.connectAttr("time1.outTime", f"{stage_shape}.time")

        ROOT_LAYER = "maya_root.usd"
        root_layer_path = str(get_production_path() / shot_path / ROOT_LAYER)
        root_layer = Sdf.Layer.FindOrOpen(root_layer_path) or Sdf.Layer.CreateNew(
            root_layer_path
        )
        root_layer.Save()
        mc.setAttr(f"{stage_shape}.filePath", "../" + ROOT_LAYER, type="string")

        # mc.mayaUsdLayerEditor(str(get_production_path() / "root.usda"), edit=True, lockLayer=(2, 0, stage_shape))

        # Set up stage
        self._setup_scene()
        root_layer.Save()
        root_layer.SetPermissionToSave(False)

        # Save USD Edits to the scene file and don't prompt about it
        mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocationPrompt", 0))
        mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocation", 2))

        # Save shot code to file
        mc.fileInfo("code", self.shot.code or "")
        mc.file(save=True, force=True)

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        """Return (stream, owner_label, shot) for the current scene, or None.

        Subclasses must override this to resolve the versioning stream that
        corresponds to the open scene file.  ``owner_label`` is displayed in
        the version browser header.  ``shot`` is passed to ``_post_open_file``
        after opening a backup version.
        """
        ...

    def _entity_label(self) -> str:
        """Human-readable noun for the entity kind managed by this class.

        Used in dialog messages, e.g. ``\"animation\"``, ``\"RLO\"``.
        """
        return "shot"

    # ------------------------------------------------------------------
    # Shared version browser and save
    # ------------------------------------------------------------------

    def open_version_browser(self) -> None:
        kind = self._entity_label()
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                f"No valid {kind} shot file is open. Use Open {kind} first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                f"Could not resolve the current scene to a valid {kind} shot file. "
                f"Use Open {kind} first.",
                "Version History",
            ).exec_()
            return

        stream, owner_label, shot = resolved
        records = list_version_records(stream)
        if not records:
            MessageDialog(
                self._main_window,
                f"No version history was found for this {kind}.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            owner_label=owner_label,
            stream_label=stream.label,
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
                log.exception("Failed to open %s backup version: %s", kind, backup_path)
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
                promoted_record = _promote_version(
                    selected_record,
                    stream,
                    title=promote_dialog.get_title(),
                    note=promote_dialog.get_note(),
                )
            except Exception as exc:
                log.exception("Failed to create a new %s version.", kind)
                MessageDialog(
                    self._main_window,
                    f"Failed to create new version:\n{exc}",
                    "Create Version Failed",
                ).exec_()
                return

            MessageDialog(
                self._main_window,
                (
                    f"Created new version {version_label(promoted_record.version)} "
                    f'"{promoted_record.title or "(untitled)"}" from the selected backup.\n'
                    "Open it from Version History to continue working from it."
                ),
                "Version Created",
            ).exec_()

    def _do_save_version_for_scene(
        self, scene_path: Path, stream: VersionStreamSpec
    ) -> None:
        """Prompt for a version title and write a backup of *scene_path*."""
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return

        try:
            version_record = _save_version(
                scene_path,
                stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save %s version.", self._entity_label())
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            (
                f"Saved {version_label(version_record.version)} "
                f'"{version_record.title or "(untitled)"}".'
            ),
            "Version Saved",
        ).exec_()

    def save_version_for_current_scene(self) -> None:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return

        resolved = self._resolve_current_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                f"Could not resolve the current scene to a valid {self._entity_label()} shot file.",
                "Shot Not Resolved",
            ).exec_()
            return

        stream, _, _ = resolved
        self._do_save_version_for_scene(scene_path, stream)
