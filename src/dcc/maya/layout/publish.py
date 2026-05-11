import os
import maya.cmds as mc
import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]
from pxr import Sdf
from typing import cast
import shutil


class MLayoutPublisher:
    """
    Publishes the environment layout from Maya as a USD stage.

    This is kept separate from other publishers because it exports
    only the USD stage and doesn't rely on standard Maya file saving.
    """

    @staticmethod
    def publish(needs_confirmation: bool = True) -> None:
        # Ensure mayaUsdPlugin is loaded
        if not mc.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            mc.loadPlugin("mayaUsdPlugin")

        # Find the first mayaUsdProxyShape
        proxy_shapes = mc.ls(type="mayaUsdProxyShape", long=True)
        if not proxy_shapes:
            mc.error("No mayaUsdProxyShape found in the scene.")
            return

        proxy_shape = proxy_shapes[0]

        try:
            prim = mayaUsdLib.GetPrim(proxy_shape)
            stage = prim.GetStage()
        except Exception as e:
            mc.error(f"Failed to get USD stage: {str(e)}")
            return

        if not stage:
            mc.error("No valid USD stage found.")
            return

        scene_path = mc.file(query=True, sceneName=True)
        if not scene_path:
            mc.error("Scene must be saved before exporting USD.")
            return

        scene_path_str = cast(str, scene_path)
        scene_dir = os.path.dirname(scene_path_str)
        save_path = os.path.join(scene_dir, "maya_layout.usd")

        if os.path.exists(save_path):
            backup_path = save_path + ".bak"
            shutil.copy2(save_path, backup_path)

        # Get the root layer and export
        try:
            root_layer: Sdf.Layer = stage.GetRootLayer()
            root_layer.Export(save_path)

            if needs_confirmation:
                mc.confirmDialog(
                    title="Publish Complete",
                    message=f"Layout successfully published to {save_path}",
                    button=["OK"],
                    defaultButton="OK",
                    dismissString="OK",
                )

        except Exception as e:
            mc.confirmDialog(
                title="Publish Failed",
                message=f"An error occurred:\n{e}",
                button=["OK"],
                defaultButton="OK",
            )
            raise
