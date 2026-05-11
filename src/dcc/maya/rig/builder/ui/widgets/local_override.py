from pathlib import Path

from Qt.QtCore import Signal
from Qt.QtWidgets import QVBoxLayout, QWidget

from dcc.maya.rig.builder.ui.widgets import DirectorySelect

from ..styling import LOCAL_OVERRIDE_COLOR
from .switch import SwitchWithLabel


class LocalOverrideOptions(QWidget):
    override_changed = Signal(bool)
    override_directory_changed = Signal(Path)

    def __init__(self, parent: QWidget | None = None, enabled: bool = False):
        super().__init__(parent)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.local_override_switch = SwitchWithLabel(
            "Local Override", color_on=LOCAL_OVERRIDE_COLOR
        )
        self.local_override_switch.toggled.connect(self.override_changed)
        self.local_override_switch.toggled.connect(self._on_toggle)
        self.main_layout.addWidget(self.local_override_switch)

        self.local_override_dir = DirectorySelect(self, "Local Rig Build Directory")
        self.local_override_dir.directory_changed.connect(
            self.override_directory_changed
        )
        self.main_layout.addWidget(self.local_override_dir)
        self.set_override(enabled)
        self.local_override_dir.setVisible(enabled)

    def _on_toggle(self, checked: bool):
        self.local_override_dir.setVisible(checked)

    @property
    def override_enabled(self) -> bool:
        return self.local_override_switch.isChecked()

    def set_override(self, enabled: bool):
        self.local_override_switch.setChecked(enabled)

    @property
    def override_directory(self) -> Path | None:
        return self.local_override_dir.get_path()

    def set_override_directory(self, directory: Path):
        self.local_override_dir.set_path(directory)
