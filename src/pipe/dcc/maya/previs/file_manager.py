"""`MPrevisFileManager` — sequence-level Maya file for the previs sequencer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import maya.cmds as mc
from pxr import Sdf

from pipe.core.shotgrid import SGEntity, Shot, is_previs_shot_code
from pipe.core.util.paths import get_previs_path
from pipe.core.versioning import VersionStreamSpec

from pipe.dcc.maya.shotfile.shotfile_manager import MShotFileManager

from . import playback, state
from .state import PrevisState

log = logging.getLogger(__name__)

_ROOT_LAYER_FILENAME = "maya_root.usd"


class MPrevisFileManager(MShotFileManager):
    def __init__(self) -> None:
        super().__init__(version_msg="Open older previs file")
        # Alternates ARE the history surface
        self._versioning = False

    def _entity_label(self) -> str:
        return "previs"

    def _check_unsaved_changes(self) -> bool:
        return True

    def _filter_entities(self, entities: list[SGEntity]) -> list[SGEntity]:
        return [e for e in entities if is_previs_shot_code(e.code)]

    def _compute_entity_path(self, entity: SGEntity) -> Path:
        shot = cast(Shot, entity)
        return get_previs_path() / (shot.code or "")

    def _setup_scene(self) -> None:
        # Sequence-level environment refs only. Per-shot env overrides remain
        # the RLO's responsibility, so there's no shot-level edit-target layer here.
        envs = list(self.shot.sets or [])
        if not envs and self.shot.set:
            envs = [self.shot.set]

        stage = self.get_stage()
        root_layer = stage.GetRootLayer()
        for env in envs:
            if env is None:
                continue
            env_layer = Sdf.Layer.FindOrOpenRelativeToLayer(
                root_layer, env.environment_path
            )
            if env_layer is None:
                log.warning("Could not open env layer: %s", env.environment_path)
                continue
            if env_layer.identifier not in root_layer.subLayerPaths:  # type: ignore[operator]
                root_layer.subLayerPaths.append(env_layer.identifier)
            env_layer.SetPermissionToSave(False)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        mc.file(newFile=True, force=True)
        mc.file(rename=str(path))

        self.shot = cast(Shot, entity)
        code = self.shot.code or ""
        previs_dir = get_previs_path() / code

        transform = mc.createNode("transform", name="stage_transform")
        mc.createNode("mayaUsdProxyShape", name="stage", parent=transform)
        stage_shape = self.get_stage_shape()
        mc.connectAttr("time1.outTime", f"{stage_shape}.time")

        root_layer_path = str(previs_dir / _ROOT_LAYER_FILENAME)
        root_layer = Sdf.Layer.FindOrOpen(root_layer_path) or Sdf.Layer.CreateNew(
            root_layer_path
        )
        root_layer.Save()
        mc.setAttr(
            f"{stage_shape}.filePath", "../" + _ROOT_LAYER_FILENAME, type="string"
        )

        self._setup_scene()
        root_layer.Save()
        root_layer.SetPermissionToSave(False)

        mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocationPrompt", 0))
        mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocation", 2))

        state.write_state(PrevisState.empty())
        mc.fileInfo("code", code)
        mc.file(save=True, force=True)

    @classmethod
    def run_on_open(cls) -> None:
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore
        playback.install_camera_callback()

    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        # Previs deliberately has no version-browser surface; stub satisfies the abstract.
        return None
