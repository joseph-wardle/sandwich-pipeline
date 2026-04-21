from __future__ import annotations

import Qt
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin  # type: ignore
from Qt.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .logbox import RigBuildLogBox
from .progress_bar import RigBuildProgressBar
from .rig_type_tabs import RigTypeTabWidget
from .test_select import TestSelectList


class RigBuilderWindowUI(MayaQWidgetDockableMixin, QWidget):
    def __init__(self, parent: QWidget | None, window_object_name: str) -> None:
        super().__init__(parent=parent)
        self.window_object_name = window_object_name
        self.setup_ui()

    def setup_ui(self) -> None:
        self.setObjectName(self.window_object_name)
        self.setWindowTitle("The Rig-Build-inator")

        # ---------- MAIN LAYOUT ----------
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 4, 8, 0)
        self.setLayout(main_layout)

        self.main_splitter = QSplitter()
        self.main_splitter.setOrientation(Qt.QtCore.Qt.Vertical)  # type: ignore
        main_layout.addWidget(self.main_splitter)

        # Build Section
        self.top_container = QWidget()
        self.main_splitter.addWidget(self.top_container)
        self.main_splitter.setStretchFactor(0, 3)

        self.top_layout = QVBoxLayout(self.top_container)
        self.top_layout.setContentsMargins(0, 0, 0, 4)
        self.build_label = QLabel()
        self.build_label.setText("Build")
        self.top_layout.addWidget(self.build_label)
        self.build_tabs = RigTypeTabWidget()
        self.top_layout.addWidget(self.build_tabs)
        self.character_select = self.build_tabs.create_tab("character", "Character")
        self.prop_select = self.build_tabs.create_tab("prop", "Prop")

        # Build Options
        self.build_horizontal_layout = QHBoxLayout()
        self.build_horizontal_layout.setContentsMargins(0, 0, 0, 0)
        self.top_layout.addLayout(self.build_horizontal_layout)

        self.dev_build_switch = QCheckBox()
        self.dev_build_switch.setText("Dev Build")
        self.build_horizontal_layout.addWidget(self.dev_build_switch, 1)

        self.build_rig_button = QPushButton()
        self.build_rig_button.setText("Build Rig")
        self.build_horizontal_layout.addWidget(self.build_rig_button, 2)

        # Test Section
        self.mid_container = QWidget()
        self.main_splitter.addWidget(self.mid_container)
        self.main_splitter.setStretchFactor(1, 2)

        self.mid_layout = QVBoxLayout(self.mid_container)
        self.mid_layout.setContentsMargins(0, 0, 0, 4)
        self.test_label = QLabel()
        self.test_label.setText("Test")
        self.mid_layout.addWidget(self.test_label)

        self.test_list = TestSelectList()
        self.mid_layout.addWidget(self.test_list)

        self.test_selection_layout = QHBoxLayout()
        self.mid_layout.addLayout(self.test_selection_layout)

        self.rig_test_button = QPushButton()
        self.rig_test_button.setText("Run Selected Tests")
        self.mid_layout.addWidget(self.rig_test_button)

        # Publish Section
        self.publish_label = QLabel()
        self.publish_label.setText("Publish")
        self.mid_layout.addWidget(self.publish_label)

        # Publish Options
        self.publish_horizontal_layout = QHBoxLayout()
        self.publish_horizontal_layout.setContentsMargins(0, 0, 0, 0)
        self.mid_layout.addLayout(self.publish_horizontal_layout)

        self.rig_version_spinbox = QDoubleSpinBox()
        self.rig_version_spinbox.setPrefix("v")
        self.rig_version_spinbox.setValue(1)
        self.rig_version_spinbox.setSingleStep(0.01)
        self.publish_horizontal_layout.addWidget(self.rig_version_spinbox, 1)

        self.rig_publish_button = QPushButton()
        self.rig_publish_button.setText("Build Test and Publish")
        self.publish_horizontal_layout.addWidget(self.rig_publish_button, 2)

        # Build Log Section
        self.rig_build_progress_bar = RigBuildProgressBar()
        self.mid_layout.addWidget(self.rig_build_progress_bar)

        self.bottom_container: QWidget = QWidget()
        self.main_splitter.addWidget(self.bottom_container)
        self.main_splitter.setStretchFactor(2, 1)
        self.bottom_layout = QVBoxLayout(self.bottom_container)
        self.bottom_layout.setContentsMargins(0, 4, 0, 0)

        self.rig_build_log_box = RigBuildLogBox()
        self.bottom_layout.addWidget(self.rig_build_log_box)
