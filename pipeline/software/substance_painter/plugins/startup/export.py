from __future__ import annotations

import pipe.sp
import substance_painter as sp
from pipe.glui.dialogs import MessageDialog
from pipe.sp.ui import SubstanceExportWindow
from Qt import QtWidgets

plugin_widgets: list[QtWidgets.QWidget | QtWidgets.QAction] = []


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


def close_plugin():
    for widget in plugin_widgets:
        sp.ui.delete_ui_element(widget)

    plugin_widgets.clear()


if __name__ == "__main__":
    window = start_plugin()


def launch_exporter():
    if not sp.project.is_open():
        MessageDialog(
            pipe.sp.local.get_main_qt_window(),
            "Please open a project before trying to publish",
            "No project open",
        ).exec_()
        return

    # remove existing windows before opening a new one
    for widget in plugin_widgets:
        if isinstance(widget, SubstanceExportWindow):
            widget.close()
            sp.ui.delete_ui_element(widget)
            plugin_widgets.remove(widget)
            break

    # launch window
    global window
    window = SubstanceExportWindow()
    window.show()

    print("Launching Substance Exporter")


def launch_asset_opener():
    from pipe.sp.assetfile import launch_open_asset_textures

    launch_open_asset_textures()


def launch_save_version():
    from pipe.sp.assetfile import launch_save_version as _launch_save_version

    _launch_save_version()


def launch_version_history():
    from pipe.sp.assetfile import (
        launch_version_browser_for_current_project as _launch_version_history,
    )

    _launch_version_history()
