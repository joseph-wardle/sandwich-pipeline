"""Painter startup plugin that adds the SKD entries to the File menu."""

from __future__ import annotations

from collections.abc import Callable

import substance_painter as sp
from Qt import QtWidgets

from env_sg import DB_Config
from pipe.core.shotgrid import ShotGrid
from pipe.dcc.substance_painter import runtime as sp_runtime
from pipe.dcc.substance_painter.assetfile import (
    launch_open_asset_textures,
    launch_save_version,
    launch_version_browser_for_current_project,
)
from pipe.dcc.substance_painter.ui import SubstanceExportWindow
from pipe.dcc.substance_painter.util.metadata import identify_open_project
from pipe.dcc.substance_painter.util.project import check_project_editable

plugin_widgets: list[QtWidgets.QWidget | QtWidgets.QAction] = []
_publish_window: SubstanceExportWindow | None = None


def start_plugin() -> None:
    menu_entries: list[tuple[str, Callable[[], None]]] = [
        ("SKD — Open Asset", launch_open_asset_textures),
        ("SKD — Save Version", launch_save_version),
        ("SKD — Version History", launch_version_browser_for_current_project),
        ("SKD — Publish Textures", launch_exporter),
    ]
    for label, launch in menu_entries:
        action = QtWidgets.QAction(label)
        action.triggered.connect(launch)
        sp.ui.add_action(sp.ui.ApplicationMenu.File, action)
        # Kept so close_plugin can remove it from the menu.
        plugin_widgets.append(action)

    sp.event.DISPATCHER.connect_strong(
        sp.event.ProjectAboutToClose, _on_project_about_to_close
    )


def close_plugin() -> None:
    sp.event.DISPATCHER.disconnect(
        sp.event.ProjectAboutToClose, _on_project_about_to_close
    )
    _close_publish_window()
    for widget in plugin_widgets:
        sp.ui.delete_ui_element(widget)

    plugin_widgets.clear()


def launch_exporter() -> None:
    """Open a fresh Publish window, or raise the one that is publishing."""
    global _publish_window
    if _publish_window is not None and _publish_window.is_publishing:
        _publish_window.raise_()
        _publish_window.activateWindow()
        return

    if not check_project_editable(sp_runtime.get_main_qt_window(), "Publish Textures"):
        return

    conn = ShotGrid.connect(DB_Config)
    identity = identify_open_project(
        conn, sp_runtime.get_main_qt_window(), "Publish Textures"
    )
    if identity is None:
        return

    _close_publish_window()
    _publish_window = SubstanceExportWindow(conn, identity)
    _publish_window.show()


def _close_publish_window() -> None:
    global _publish_window
    # close() is refused while a publish is running.
    if _publish_window is not None and _publish_window.close():
        _publish_window.deleteLater()
        _publish_window = None


def _on_project_about_to_close(_event: sp.event.Event) -> None:
    _close_publish_window()
