from __future__ import annotations

import logging
from pathlib import Path

from maya.OpenMayaUI import MQtUtil
from Qt import QtCore
from Qt.QtWidgets import QWidget

from pipe.m.local import get_main_qt_window

from .. import build, publish
from ..build import RigDefinition, has_local_override_directory
from ..database import DBWorker
from .core import (
    delete_workspace_control,
    get_maya_main_window,
)
from .settings import RigBuilderSettings
from .window_ui import RigBuilderWindowUI

log = logging.getLogger(__name__)

_window_instance: RigBuilderWindow | None = None

WINDOW_OBJECT_NAME = "rigBuilderWindow"
WORKSPACE_CONTROL_NAME = WINDOW_OBJECT_NAME + "WorkspaceControl"


def _restore() -> None:
    """Called by Maya's workspaceControl restore mechanism."""
    global _window_instance

    # Always recreate the widget on restore
    _window_instance = RigBuilderWindow(parent=get_maya_main_window())

    # Locate the workspace control that Maya already created.
    workspace_ptr = MQtUtil.findControl(WORKSPACE_CONTROL_NAME)
    # Get a pointer to our widget so we can hand it to Maya.
    widget_ptr = MQtUtil.findControl(_window_instance.objectName())
    if workspace_ptr and widget_ptr:
        MQtUtil.addWidgetToMayaLayout(int(widget_ptr), int(workspace_ptr))


# This uiScript is called by Maya to recreate the widget when restoring layout.
# Here we generate the import and command run lines to make it easy to rename things with IDE tools without breaking this.
UI_SCRIPT = f"""
import {__name__}
{__name__}.{_restore.__name__}()
"""


def close() -> None:
    global _window_instance
    if _window_instance is not None:
        _window_instance.close()


def launch() -> None:
    global _window_instance

    delete_workspace_control(WORKSPACE_CONTROL_NAME)

    _window_instance = RigBuilderWindow(parent=get_main_qt_window())
    _window_instance.show(
        dockable=True,
        uiScript=UI_SCRIPT,
        workspaceControlName=WORKSPACE_CONTROL_NAME,
    )


class RigBuilderWindow(RigBuilderWindowUI):
    def __init__(
        self,
        parent: QWidget | None,
    ) -> None:
        super().__init__(parent=parent, window_object_name=WINDOW_OBJECT_NAME)
        self.threads: list[QtCore.QThread] = []
        self._load_settings()
        self.connect_ui()
        self.load_data_async()  # Start loading after UI is initialized

    def connect_ui(self):
        builder_log = logging.getLogger("pipe.m.rig.builder")
        builder_log.setLevel(logging.DEBUG)
        self.rig_build_log_box.connect_logger(builder_log)

        self.build_tabs.currentChanged.connect(self._on_tab_changed)
        self.character_select.rig_changed.connect(
            lambda rig_name: setattr(
                RigBuilderSettings.LAST_CHARACTER_RIG, "value", rig_name
            )
        )
        self.character_select.variant_changed.connect(
            lambda variant_name: setattr(
                RigBuilderSettings.LAST_CHARACTER_VARIANT, "value", variant_name
            )
        )
        self.prop_select.rig_changed.connect(
            lambda rig_name: setattr(
                RigBuilderSettings.LAST_PROP_RIG, "value", rig_name
            )
        )
        self.prop_select.variant_changed.connect(
            lambda variant_name: setattr(
                RigBuilderSettings.LAST_PROP_VARIANT, "value", variant_name
            )
        )
        self.rig_build_scope_select.selection_changed.connect(
            lambda chip_label: setattr(
                RigBuilderSettings.LAST_BUILD_SCOPE, "value", chip_label
            )
        )
        self.dev_build_switch.toggled.connect(
            lambda checked: setattr(RigBuilderSettings.DEV_BUILD, "value", checked)
        )
        self.local_override_options.override_changed.connect(
            lambda checked: setattr(RigBuilderSettings.LOCAL_OVERRIDE, "value", checked)
        )
        self.local_override_options.override_directory_changed.connect(
            lambda path: setattr(
                RigBuilderSettings.LAST_OVERRIDE_DIR, "value", str(path)
            )
        )
        self.local_override_options.override_changed.connect(
            lambda _: self._refresh_override_indicators()
        )

        self.local_override_options.override_directory_changed.connect(
            lambda _: self._refresh_override_indicators()
        )

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

    def _load_settings(self):
        self.build_tabs.set_current_tab(RigBuilderSettings.LAST_TAB.value)
        self.dev_build_switch.setChecked(RigBuilderSettings.DEV_BUILD.value)
        self.local_override_options.set_override(
            RigBuilderSettings.LOCAL_OVERRIDE.value
        )
        self.local_override_options.set_override_directory(
            Path(RigBuilderSettings.LAST_OVERRIDE_DIR.value)
        )

    def _on_dev_build_changed(self, checked: bool):
        RigBuilderSettings.DEV_BUILD.value = checked

    def _on_tab_changed(self, index: int):
        RigBuilderSettings.LAST_TAB.value = index

    def _on_rig_data_received(
        self, characters: list[tuple[str, str]], props: list[tuple[str, str]]
    ):
        """Update the UI widgets once the DB query returns."""
        self.character_select.populate_rigs(characters)
        self.prop_select.populate_rigs(props)

        self.character_select.select_rig(RigBuilderSettings.LAST_CHARACTER_RIG.value)
        self.prop_select.select_rig(RigBuilderSettings.LAST_PROP_RIG.value)
        # TODO: Actually handle variants here, when changing the selected rig, and in the build.
        self.character_select.populate_variants(["default"])
        self.character_select.select_variant(
            RigBuilderSettings.LAST_CHARACTER_VARIANT.value
        )
        self.prop_select.populate_variants(["default"])
        self.prop_select.select_variant(RigBuilderSettings.LAST_PROP_VARIANT.value)
        self.rig_build_scope_select.select_chip(
            RigBuilderSettings.LAST_BUILD_SCOPE.value
        )
        self._refresh_override_indicators()

    def _compute_override_rigs(self, rig_names: list[str], rig_type: str) -> list[str]:
        override_dir = self.local_override_options.override_directory
        use_override = self.local_override_options.override_enabled

        if not use_override:
            return []

        return [
            name
            for name in rig_names
            if has_local_override_directory(
                RigDefinition(name=name, type=rig_type),
                override_dir,
            )
        ]

    def _refresh_override_indicators(self):
        self.character_select.set_override_rigs(
            self._compute_override_rigs(
                self.character_select.get_all_rig_names(),
                rig_type=self.character_select.name,
            )
        )

        self.prop_select.set_override_rigs(
            self._compute_override_rigs(
                self.prop_select.get_all_rig_names(),
                rig_type=self.prop_select.name,
            )
        )

    def _get_rig_to_build(self) -> RigDefinition | None:
        current_tab = self.build_tabs.get_current_tab()
        rig_type = current_tab.get_rig_type()
        selected_rig = current_tab.get_selected_rig()
        if selected_rig is not None:
            return RigDefinition(selected_rig, rig_type)
        else:
            return None

    def _build_rig(self):
        rig_builder = build.RigBuilder()
        dev_build = self.dev_build_switch.isChecked()
        rig_to_build = self._get_rig_to_build()
        rig_builder.connect_progress(self.rig_build_progress_bar.update_progress)
        if rig_to_build is not None:
            rig_builder.build_rig(rig_to_build, dev_build=dev_build)

    def _build_test_publish(self):
        rig_publisher = publish.RigPublisher()
        rig_publisher.connect_progress(self.rig_build_progress_bar.update_progress)
        rig_publisher.connect_test_view(self.test_list.on_test_finished)
        rig_to_build = self._get_rig_to_build()
        if rig_to_build is None:
            log.error("Failed to build rig: no rig is selected.")
            return
        rig_publisher.build_test_and_publish(rig_to_build)
