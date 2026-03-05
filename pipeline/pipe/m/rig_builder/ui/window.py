from __future__ import annotations

import logging

from Qt import QtCore
from maya.OpenMayaUI import MQtUtil
from Qt.QtWidgets import QWidget

from ...local import get_main_qt_window
from .. import build
from .. import publish
from .core import (
    delete_workspace_control,
    get_maya_main_window,
)
from .window_ui import RigBuilderWindowUI
from ..database import DBWorker

log = logging.getLogger(__name__)

_window_instance: RigBuilderWindow | None = None

WINDOW_OBJECT_NAME = "rigBuilderWindow"
WORKSPACE_CONTROL_NAME = WINDOW_OBJECT_NAME + "WorkspaceControl"

# This uiScript is called by Maya to recreate the widget when restoring layout.
# It must be a string that Maya can evaluate via Python.
UI_SCRIPT = """
import pipe.m.rig_builder.ui
pipe.m.rig_builder.ui.window._restore()
"""


def _restore() -> None:
    """Called by Maya's workspaceControl restore mechanism."""
    global _window_instance

    # Always recreate the widget on restore
    _window_instance = RigBuilderWindow(parent=get_maya_main_window())  # type: ignore

    # Locate the workspace control that Maya already created.
    workspace_ptr = MQtUtil.findControl(WORKSPACE_CONTROL_NAME)
    # Get a pointer to our widget so we can hand it to Maya.
    widget_ptr = MQtUtil.findControl(_window_instance.objectName())
    if workspace_ptr and widget_ptr:
        MQtUtil.addWidgetToMayaLayout(int(widget_ptr), int(workspace_ptr))


def close() -> None:
    global _window_instance
    if _window_instance is not None:
        _window_instance.close()


def launch() -> None:
    global _window_instance

    delete_workspace_control(WORKSPACE_CONTROL_NAME)

    _window_instance = RigBuilderWindow(parent=get_main_qt_window())  # type: ignore
    _window_instance.show(
        dockable=True,  # type: ignore
        uiScript=UI_SCRIPT,  # type: ignore
        workspaceControlName=WORKSPACE_CONTROL_NAME,  # type: ignore
    )


class RigBuilderWindow(RigBuilderWindowUI):
    def __init__(
        self,
        parent: QWidget | None,
    ) -> None:
        super().__init__(parent=parent, window_object_name=WINDOW_OBJECT_NAME)
        self.threads: list[QtCore.QThread] = []
        self.connect_ui()
        self.load_data_async()  # Start loading after UI is initialized

    def connect_ui(self):
        builder_log = logging.getLogger("pipe.m.rig_builder")
        builder_log.setLevel(logging.DEBUG)
        self.rig_build_log_box.connect_logger(builder_log)

        self.build_rig_button.clicked.connect(self.rig_build_log_box.clear_log)
        self.build_rig_button.clicked.connect(self._build_rig)

        self.rig_test_button.clicked.connect(self.rig_build_log_box.clear_log)
        self.rig_test_button.clicked.connect(self.test_list.run_tests)
        self.test_list.connect_progress(self.rig_build_progress_bar.update_progress)

        self.rig_publish_button.clicked.connect(self._build_test_publish)

    def load_data_async(self):
        """Spawns a thread to fetch DB data without freezing the UI."""

        self.db_thread = QtCore.QThread()
        self.db_worker = DBWorker()
        self.db_worker.moveToThread(self.db_thread)
        # Connect signals
        self.db_thread.started.connect(self.db_worker.get_rig_data)
        self.db_worker.rigs_loaded.connect(self._on_rig_data_received)

        # Cleanup
        self.db_worker.rigs_loaded.connect(self.db_thread.quit)
        self.db_worker.rigs_loaded.connect(self.db_worker.deleteLater)
        self.db_thread.finished.connect(self.db_thread.deleteLater)

        self.threads.append(self.db_thread)
        self.db_thread.start()

    def _on_rig_data_received(
        self, characters: list[tuple[str, str]], props: list[tuple[str, str]]
    ):
        """Update the UI widgets once the DB query returns."""
        self.character_select.populate_rigs(characters)  # Update your widget method
        self.prop_select.populate_rigs(props)
        # TODO: Actually handle variants here, when changing the selected rig, and in the build.
        self.character_select.populate_variants(["default"])
        self.prop_select.populate_variants(["default"])

    def _get_rig_to_build(self) -> tuple[str, str] | None:
        current_tab = self.build_tabs.get_current_tab()
        rig_type = current_tab.get_rig_type()
        selected_rig = current_tab.get_selected_rig()
        if selected_rig is not None:
            return (selected_rig, rig_type)
        else:
            return None

    def _build_rig(self):
        rig_builder = build.RigBuilder()
        dev_build = (
            True
            if self.dev_build_switch.checkState() == QtCore.Qt.CheckState.Checked
            else False
        )
        current_tab = self.build_tabs.get_current_tab()
        rig_type = current_tab.get_rig_type()
        selected_rig = current_tab.get_selected_rig()
        rig_builder.connect_progress(self.rig_build_progress_bar.update_progress)
        if selected_rig is not None:
            rig_builder.build_rig(selected_rig, rig_type=rig_type, dev_build=dev_build)

    def _build_test_publish(self):
        rig_publisher = publish.RigPublisher()
        rig_publisher.connect_progress(self.rig_build_progress_bar.update_progress)
        rig_publisher.connect_test_view(self.test_list.on_test_finished)
        rig_to_build = self._get_rig_to_build()
        if rig_to_build is None:
            log.error("Failed to build rig: no rig is selected.")
            return
        rig_publisher.build_test_and_publish(rig_to_build[0], rig_to_build[1])
