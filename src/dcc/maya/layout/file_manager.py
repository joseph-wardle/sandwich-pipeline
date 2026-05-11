from __future__ import annotations

import logging
from pathlib import Path

import maya.cmds as mc
import mayaUsd  # type: ignore[import-not-found] # noqa: F401
import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]
from env_sg import DB_Config
from mayaUsd.lib import proxyAccessor as pa
from pxr import Gf, Usd, UsdGeom
from core.util.util import get_production_path

from dcc.maya.runtime import get_main_qt_window
from core.shotgrid import Environment, SGEntity, ShotGrid
from core.util import FileManager, log_errors

from .publish import MLayoutPublisher

log = logging.getLogger(__name__)

HOUDINI_TO_MAYA_SCALE = Gf.Vec3d(100.0, 100.0, 100.0)


class MLayoutFileManager(FileManager):
    set: Environment

    def __init__(self, **kwargs) -> None:
        conn = ShotGrid.connect(DB_Config)
        window = get_main_qt_window()
        super().__init__(conn, Environment, window, versioning=True, **kwargs)

    # Script to select reference when selecting the geo in the viewport, so transformations export correctly
    @staticmethod
    def change_usd_selection():
        # Get current selection
        stagePath, sdfPath = pa.getSelectedDagAndPrim()
        if sdfPath is None or stagePath is None:
            return

        if str(sdfPath)[-3:] != "geo":
            return

        # Get USD stage from DAG path
        stage = mayaUsdLib.GetPrim(stagePath).GetStage()
        if not stage:
            return

        prim = stage.GetPrimAtPath(sdfPath)
        if not prim or not prim.IsValid():
            return

        # Go two parents up
        parent1 = prim.GetParent()
        parent2 = parent1.GetParent() if parent1 else None
        parent3 = parent2.GetParent() if parent2 else None

        if parent3 and parent3.IsValid():
            # Set new selection to the grandparent prim
            newPath = f"{stagePath},{parent3.GetPath()}"
            mc.select(newPath, replace=True)

    @classmethod
    def get_stage_shape(cls) -> str:
        if ss := mc.ls(type="mayaUsdProxyShape", long=True)[0]:
            return ss
        raise RuntimeError("No USD stage found in scene")

    @classmethod
    def get_stage(cls) -> Usd.Stage:
        return mc.ls(type="mayaUsdProxyShape", long=True)[0]  # type: ignore

    @classmethod
    @log_errors
    def run_on_open(cls) -> None:
        """Function to run on file open via script node"""

        # change default render resolution
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore

        mc.scriptJob(
            event=("SelectionChanged", MLayoutFileManager.change_usd_selection),
            protected=True,
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

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        return "maya_layout", "mb"

    def _open_file(self, path: Path) -> None:
        mc.file(str(path), open=True, force=True)

    def _post_open_file(self, entity: SGEntity) -> None:
        """create `boboOnOpen` script node"""
        ON_OPEN_SCRIPT = "boboOnOpen"

        # set save path
        if not entity.path:
            mc.error("entity has no file path")
            return

        entity_path = get_production_path() / entity.path
        filename, ext = self._generate_filename_ext(entity)
        file_path = entity_path / f"{filename}.{ext}"
        mc.file(rename=str(file_path))
        mc.file(save=True, type="mayaBinary")

        if mc.objExists(ON_OPEN_SCRIPT):
            return

        classname = self.__class__.__name__
        mc.scriptNode(
            beforeScript=(
                f"from dcc.maya.layout import {classname};"
                f"{classname}.{self.__class__.run_on_open.__name__}()"
            ),
            name=ON_OPEN_SCRIPT,
            scriptType=1,
            sourceType="python",
        )
        # script node is created, will not run this session, so run manually
        self.run_on_open()

    def _setup_file(self, path: Path, entity) -> None:
        mc.file(newFile=True, force=True)
        # set save path
        entity_path = get_production_path() / entity.path
        filename, ext = self._generate_filename_ext(entity)
        file_path = entity_path / f"{filename}.{ext}"
        mc.file(rename=str(file_path))
        mc.file(save=True, type="mayaBinary")

        # Ensure mayaUsdPlugin is loaded
        if not mc.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            mc.loadPlugin("mayaUsdPlugin")

        # Create transform and proxyShape nodes
        proxy_transform = mc.createNode("transform", name="main")
        proxy_shape = mc.createNode(
            "mayaUsdProxyShape", name="mainShape", parent=proxy_transform
        )

        stage = self.get_stage()
        if not stage:
            mc.error("Could not get USD stage from proxy shape.")
            return

        # Define new Xforms
        new_xform_path = "/environment"
        environment_xform = UsdGeom.Xform.Define(stage, new_xform_path)

        scale_op = environment_xform.AddScaleOp()
        scale_op.Set(HOUDINI_TO_MAYA_SCALE)

        new_xform_path = f"/environment/{entity.name}"
        UsdGeom.Xform.Define(stage, new_xform_path)

        # Initial publish and pull back in so relative paths work
        MLayoutPublisher().publish(needs_confirmation=False)

        mc.file(newFile=True, force=True)
        mc.file(rename=str(file_path))
        mc.file(save=True, type="mayaBinary")

        proxy_transform = mc.createNode("transform", name="main")
        proxy_shape = mc.createNode(
            "mayaUsdProxyShape", name="mainShape", parent=proxy_transform
        )

        # Set the file path attribute of the proxyShape node
        mc.setAttr(
            proxy_shape + ".filePath", f"{entity_path}/maya_layout.usd", type="string"
        )

        mc.scriptJob(
            event=("SelectionChanged", MLayoutFileManager.change_usd_selection),
            protected=True,
        )
