from __future__ import annotations

import substance_painter as sp
from env_sg import DB_Config
from pipe.core.shotgrid import ShotGrid
from pipe.core.ui import MessageDialog
from pipe.dcc.substance_painter import runtime as sp_runtime
from pipe.dcc.substance_painter.ui import SubstanceExportWindow
from pipe.dcc.substance_painter.util.metadata import get_active_asset_from_project
from pipe.dcc.substance_painter.util.project import check_project_editable
from Qt import QtWidgets

plugin_widgets: list[QtWidgets.QWidget | QtWidgets.QAction] = []
_publish_window: SubstanceExportWindow | None = None


def start_plugin():
    # Create text widget for menu (Open Asset)
    open_action = QtWidgets.QAction("SKD — Open Asset")
    open_action.triggered.connect(launch_asset_opener)

    save_version_action = QtWidgets.QAction("SKD — Save Version")
    save_version_action.triggered.connect(launch_save_version)

    version_history_action = QtWidgets.QAction("SKD — Version History")
    version_history_action.triggered.connect(launch_version_history)

    # Create text widget for menu
    action = QtWidgets.QAction("SKD — Publish Textures")
    action.triggered.connect(launch_exporter)

    # Add widget to the File menu
    sp.ui.add_action(sp.ui.ApplicationMenu.File, open_action)
    sp.ui.add_action(sp.ui.ApplicationMenu.File, save_version_action)
    sp.ui.add_action(sp.ui.ApplicationMenu.File, version_history_action)
    sp.ui.add_action(sp.ui.ApplicationMenu.File, action)

    # Store the widget for proper cleanup later
    plugin_widgets.append(open_action)
    plugin_widgets.append(save_version_action)
    plugin_widgets.append(version_history_action)
    plugin_widgets.append(action)

    sp.event.DISPATCHER.connect_strong(
        sp.event.ProjectAboutToClose, _on_project_about_to_close
    )


def close_plugin():
    sp.event.DISPATCHER.disconnect(
        sp.event.ProjectAboutToClose, _on_project_about_to_close
    )
    _close_publish_window()
    for widget in plugin_widgets:
        sp.ui.delete_ui_element(widget)

    plugin_widgets.clear()


if __name__ == "__main__":
    window = start_plugin()


def launch_exporter():
    """Open a fresh Publish window, or raise the one that is publishing."""
    global _publish_window
    if _publish_window is not None and _publish_window.is_publishing:
        _publish_window.raise_()
        _publish_window.activateWindow()
        return

    if not check_project_editable(sp_runtime.get_main_qt_window(), "Publish Textures"):
        return

    conn = ShotGrid.connect(DB_Config)
    asset = get_active_asset_from_project(conn)
    if asset is None:
        MessageDialog(
            sp_runtime.get_main_qt_window(),
            "Could not resolve the current asset from project metadata. "
            "Use Open Asset to create or open the asset project first.",
        ).exec_()
        return

    _close_publish_window()
    _publish_window = SubstanceExportWindow(conn, asset)
    _publish_window.show()


def _close_publish_window() -> None:
    global _publish_window
    # close() is refused while a publish is running.
    if _publish_window is not None and _publish_window.close():
        _publish_window.deleteLater()
        _publish_window = None


def _on_project_about_to_close(_event: sp.event.Event) -> None:
    _close_publish_window()


def launch_asset_opener():
    from pipe.dcc.substance_painter.assetfile import launch_open_asset_textures

    launch_open_asset_textures()


def launch_save_version():
    from pipe.dcc.substance_painter.assetfile import (
        launch_save_version as _launch_save_version,
    )

    _launch_save_version()


def launch_version_history():
    from pipe.dcc.substance_painter.assetfile import (
        launch_version_browser_for_current_project as _launch_version_history,
    )

    _launch_version_history()
