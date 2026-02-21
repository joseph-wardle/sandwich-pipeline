from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Iterable, Optional, cast

import maya.api.OpenMaya as om
import maya.cmds as mc
import mayaUsd  # type: ignore[import-not-found]
from env_sg import DB_Config
from pxr import Sdf, Usd, UsdGeom
from shared.util import get_production_path
from timeline_marker.ui import TimelineMarker  # type: ignore[import-not-found]

from pipe.db import DB
from pipe.m.local import get_main_qt_window
from pipe.struct.db import SGEntity, Shot, build_shot_path, validate_shot_code_token
from pipe.util import FileManager, log_errors

from .timeline import shot_timeline_generator

log = logging.getLogger(__name__)


class MShotFileManager(FileManager):
    MAYA_OVERRIDE = "maya_override.usd"
    FOREST_LAYOUT_NAME = "Forest_layout"
    FOREST_OVERRIDE_USD = "/groups/bobo/production/set/Forest_layout/maya.usd"
    LEGACY_FOREST_OVERRIDE_USD = "/groups/bobo/set/Forest_layout/maya.usd"
    FOREST_PRIM_PATH = "/main/Forest_layout"
    shot: Shot

    def __init__(self, **kwargs) -> None:
        conn = DB.Get(DB_Config)
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
    def _shot_code_from_scene_path(cls, scene_path: Optional[str]) -> Optional[str]:
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
    def _path_has_layout_name(cls, path: str, layout_name: str) -> bool:
        parts = [part for part in cls._normalize_usd_path(path).split("/") if part]
        return layout_name in parts

    @classmethod
    def _resolve_layout_usd_path(cls, layout_path: str) -> str:
        normalized_path = cls._normalize_usd_path(layout_path)
        if normalized_path == cls.LEGACY_FOREST_OVERRIDE_USD:
            normalized_path = cls.FOREST_OVERRIDE_USD
        if cls._path_has_layout_name(normalized_path, cls.FOREST_LAYOUT_NAME):
            forest_override = cls.FOREST_OVERRIDE_USD
            if Path(forest_override).exists():
                return forest_override
            log.warning(
                "Forest override USD not found at %s; falling back to %s",
                forest_override,
                layout_path,
            )
            if normalized_path.endswith(".usd"):
                return layout_path
            return "/".join((layout_path, "main.usd"))
        if normalized_path.endswith(".usd"):
            return layout_path
        return "/".join((layout_path, "main.usd"))

    @classmethod
    def _ensure_sublayer(
        cls,
        root_layer: Sdf.Layer,
        layer_path: str,
        *,
        label: str,
        insert_after: Optional[str] = None,
    ) -> Optional[Sdf.Layer]:
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
    def _ensure_forest_mount(
        cls,
        override_layer: Sdf.Layer,
        forest_usd_path: str,
    ) -> bool:
        forest_usd_path = cls._normalize_usd_path(forest_usd_path)
        if not Path(forest_usd_path).exists():
            log.warning(
                "Forest USD not found at %s; cannot mount under /environment",
                forest_usd_path,
            )
            return False

        override_layer.Save()
        try:
            stage = Usd.Stage.Open(override_layer.identifier)
        except Exception:
            log.exception(
                "Unable to open override layer %s for forest mount",
                override_layer.identifier,
            )
            return False

        stage.SetEditTarget(Usd.EditTarget(override_layer))
        stage.DefinePrim("/environment", "Xform")
        forest_prim = stage.DefinePrim(
            f"/environment/{cls.FOREST_LAYOUT_NAME}", "Xform"
        )
        refs = forest_prim.GetReferences()
        refs.ClearReferences()
        refs.AddReference(forest_usd_path, cls.FOREST_PRIM_PATH)
        stage.OverridePrim(cls.FOREST_PRIM_PATH).SetActive(False)
        override_layer.Save()
        return True

    @classmethod
    def _find_root_layer_path(cls, scene_path: Path) -> Optional[Path]:
        for parent in scene_path.parents:
            candidate = parent / "maya_root.usd"
            if candidate.exists():
                return candidate
        return None

    @classmethod
    def _guard_forest_layout_before_open(cls, scene_path: Path) -> None:
        root_layer_path = cls._find_root_layer_path(scene_path)
        if not root_layer_path:
            return

        root_layer = Sdf.Layer.FindOrOpen(str(root_layer_path))
        if not root_layer:
            return

        original_paths = list(cast(Iterable[str], root_layer.subLayerPaths))
        forest_paths: list[str] = []

        for sub_path in original_paths:
            normalized_path = cls._normalize_usd_path(sub_path)
            is_legacy_override = normalized_path == cls.LEGACY_FOREST_OVERRIDE_USD
            is_override = normalized_path == cls.FOREST_OVERRIDE_USD
            is_forest_main = cls._path_has_layout_name(
                normalized_path, cls.FOREST_LAYOUT_NAME
            ) and normalized_path.endswith("/main.usd")
            if is_legacy_override or is_override or is_forest_main:
                forest_paths.append(sub_path)

        if not forest_paths:
            return

        forest_mount_path = cls._resolve_layout_usd_path(forest_paths[0])
        override_layer_path = root_layer_path.parent / "set" / cls.MAYA_OVERRIDE
        override_layer = Sdf.Layer.FindOrOpen(
            str(override_layer_path)
        ) or Sdf.Layer.CreateNew(str(override_layer_path))

        mounted = False
        if override_layer:
            mounted = cls._ensure_forest_mount(override_layer, forest_mount_path)

        if mounted:
            forest_set = set(forest_paths)
            new_paths = [p for p in original_paths if p not in forest_set]
            if override_layer and override_layer.identifier not in new_paths:
                new_paths.append(override_layer.identifier)
            if new_paths != original_paths:
                root_layer.subLayerPaths[:] = new_paths
                root_layer.Save()
            return

        # Fallback: replace legacy/main forest paths with the override if available
        updated = False
        fallback_paths: list[str] = []
        override_exists = Path(cls.FOREST_OVERRIDE_USD).exists()
        for sub_path in original_paths:
            normalized_path = cls._normalize_usd_path(sub_path)
            is_legacy_override = normalized_path == cls.LEGACY_FOREST_OVERRIDE_USD
            is_forest_main = cls._path_has_layout_name(
                normalized_path, cls.FOREST_LAYOUT_NAME
            ) and normalized_path.endswith("/main.usd")
            if (is_legacy_override or is_forest_main) and override_exists:
                if cls.FOREST_OVERRIDE_USD not in fallback_paths:
                    fallback_paths.append(cls.FOREST_OVERRIDE_USD)
                updated = True
                continue
            fallback_paths.append(sub_path)

        if updated:
            root_layer.subLayerPaths[:] = fallback_paths
            root_layer.Save()

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
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore[arg-type]
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore[arg-type]
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore[arg-type]
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore[arg-type]

        # set session USD target layer to the override layer
        try:
            shot_code: Optional[str] = None
            info = mc.fileInfo("code", query=True)
            if isinstance(info, (list, tuple)):
                if info:
                    shot_code = info[0]
            elif isinstance(info, str):
                shot_code = info
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
            mc.mayaUsdEditTarget(  # type: ignore[attr-defined]
                cls.get_stage_shape(),
                edit=True,
                editTarget=cls._edit_target_path_for_shot(shot_code),
            )

            conn = DB.Get(DB_Config)
            shot = conn.get_shot_by_code(shot_code)

            # Import Timeline
            frames, colors, comments = shot_timeline_generator(shot.cut_duration)
            TimelineMarker.clear()
            TimelineMarker.set(frames, colors, comments)
            mc.playbackOptions(
                animationStartTime=frames[0],
                animationEndTime=frames[-1],
                minTime=frames[0],
                maxTime=frames[-1],
            )
        except Exception:
            mc.error("Warning! Could not set edit target!")

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

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        shot = cast(Shot, entity)
        return shot.code, "mb"

    def _open_file(self, path: Path) -> None:
        self._guard_forest_layout_before_open(path)
        mc.file(str(path), open=True, force=True)

    def _post_open_file(self, entity: SGEntity) -> None:
        """create `lndOnOpen` script node"""
        ON_OPEN_SCRIPT = "lndOnOpen"

        if mc.objExists(ON_OPEN_SCRIPT):
            return

        classname = self.__class__.__name__
        mc.scriptNode(
            beforeScript=(
                f"from pipe.m.shotfile import {classname};"
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

        env_stubs = self.shot.sets
        if env_stubs:
            for env_stub in env_stubs:
                layout = self._conn.get_env_by_stub(env_stub)
                if layout and layout.path:
                    env_path = self._resolve_layout_usd_path(layout.path)
                    if (
                        self._path_has_layout_name(layout.path, self.FOREST_LAYOUT_NAME)
                        and env_override_layer
                    ):
                        if self._ensure_forest_mount(env_override_layer, env_path):
                            continue
                    env_file_layer = self._ensure_sublayer(
                        root_layer,
                        env_path,
                        label=f"environment layout ({layout.path})",
                    )
                    if env_file_layer:
                        # locked_layers.append(env_file_layer.identifier)
                        env_file_layer.SetPermissionToSave(False)
        else:
            # Fallback to depreciated single set logic if no sets are assigned
            if not (env_stub := self.shot.set):  # type: ignore[assignment]
                if not self.shot.sequence:
                    env_stub = None
                else:
                    env_stub = self._conn.get_sequence_by_stub(self.shot.sequence).set

            if env_stub and (env := self._conn.get_env_by_stub(env_stub)) and env.path:
                env_path = self._resolve_layout_usd_path(env.path)
                if (
                    self._path_has_layout_name(env.path, self.FOREST_LAYOUT_NAME)
                    and env_override_layer
                ):
                    if self._ensure_forest_mount(env_override_layer, env_path):
                        return
                env_file_layer = self._ensure_sublayer(
                    root_layer,
                    env_path,
                    label=f"environment layout ({env.path})",
                )
                if env_file_layer:
                    # locked_layers.append(env_file_layer.identifier)
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
        mc.fileInfo("code", self.shot.code)
        mc.file(save=True, force=True)
