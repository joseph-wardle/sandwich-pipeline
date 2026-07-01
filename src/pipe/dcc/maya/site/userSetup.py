"""Initialize Maya environment on startup."""

import os

import maya.cmds as mc


def main():
    # Enable required plugins
    plugins = ["mayaUsdPlugin", "pipeline_plugin.py"]
    pluginInfo: str = mc.pluginInfo(q=True, listPlugins=True)  # type: ignore
    for plugin in plugins:
        if plugin not in pluginInfo:
            mc.loadPlugin(plugin)

    # Apply the pipeline OCIO viewport default
    from pipe.core.color import DEFAULT_VIEW, DISPLAY

    mc.colorManagementPrefs(edit=True, cmEnabled=True)
    mc.colorManagementPrefs(edit=True, viewTransformName=f"{DEFAULT_VIEW} ({DISPLAY})")

    from pipe.core.util.paths import get_production_path

    # set workspace
    mc.workspace(str(get_production_path().parent), openWorkspace=True)

    # enable timeline-marker plugin
    from timeline_marker import install  # type: ignore[import-not-found]

    install.execute()

    # register USD Export chaser
    import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]

    from pipe.dcc.maya.publish import ExportChaser

    mayaUsdLib.ExportChaser.Register(ExportChaser, ExportChaser.ID)

    # Optional pipeline menu entries
    if os.getenv("PIPE_MAYA_ASSET_MENU", "0") == "1":
        from pipe.dcc.maya.assetfile import install_asset_menu

        install_asset_menu(create_menu=os.getenv("PIPE_MAYA_CREATE_MENU", "0") == "1")


if not mc.about(batch=True):
    mc.evalDeferred(main)
