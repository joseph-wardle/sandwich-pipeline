from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from Qt import QtCore, QtWidgets
from shared.util import get_edit_path

from pipe.glui.dialogs import DialogButtons

from .constants import DEFAULT_RESOLUTION
from .paths import build_output_base_paths

if TYPE_CHECKING:
    from pipe.db import DB

DEPARTMENTS = ("anim", "comp", "fx", "lighting", "previs")


class HPlayblastDialog(QtWidgets.QDialog, DialogButtons):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        conn: "DB",
        default_shot_code: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn  # remove this if you remove the parameter

        self._init_buttons(True, "Playblast", "Cancel")
        self.setWindowTitle("Houdini Playblast")

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self._shot_field = QtWidgets.QLineEdit(default_shot_code or "")
        self._shot_field.setReadOnly(True)
        form.addRow("Shot", self._shot_field)

        self._dept_combo = QtWidgets.QComboBox()
        self._dept_combo.addItems(DEPARTMENTS)
        form.addRow("Department", self._dept_combo)

        self._export_path_label = QtWidgets.QLabel(
            "Select a department to preview output"
        )
        self._export_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addRow("Export Path", self._export_path_label)

        w, h = DEFAULT_RESOLUTION
        resolution_label = QtWidgets.QLabel(f"{w}x{h}")
        resolution_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addRow("Resolution", resolution_label)

        self._custom_export_cb = QtWidgets.QCheckBox("Additional export path")
        self._custom_export_field = QtWidgets.QLineEdit()
        self._custom_export_field.setPlaceholderText("Select a folder")
        self._custom_export_button = QtWidgets.QPushButton("Browse")

        custom_row = QtWidgets.QHBoxLayout()
        custom_row.addWidget(self._custom_export_field)
        custom_row.addWidget(self._custom_export_button)

        self._custom_export_row = QtWidgets.QWidget()
        self._custom_export_row.setLayout(custom_row)
        form.addRow(self._custom_export_cb, self._custom_export_row)

        self._upload_cb = QtWidgets.QCheckBox(
            "Upload to ShotGrid (not yet implemented)"
        )
        layout.addWidget(self._upload_cb)

        layout.addWidget(self.buttons)

        self._dept_combo.currentTextChanged.connect(self._update_export_paths)
        self._custom_export_cb.toggled.connect(self._toggle_custom_export)
        self._custom_export_field.textChanged.connect(self._update_export_paths)
        self._custom_export_button.clicked.connect(self._browse_custom_export_dir)

        self._custom_export_row.setVisible(False)
        self._update_export_paths()

    @property
    def shot_code(self) -> str:
        return self._shot_field.text().strip()

    @property
    def department(self) -> str:
        return self._dept_combo.currentText()

    @property
    def upload_to_shotgrid(self) -> bool:
        return self._upload_cb.isChecked()

    def resolve_output_base_paths(self) -> tuple[Path | None, Path | None]:
        shot_code = self.shot_code
        if not shot_code:
            return None, None

        custom_dir = (
            self._custom_export_dir() if self._custom_export_cb.isChecked() else None
        )
        return build_output_base_paths(
            self.department,
            shot_code,
            custom_dir=custom_dir,
        )

    @property
    def output_base_path(self) -> Path | None:
        output_base, _ = self.resolve_output_base_paths()
        return output_base

    @property
    def custom_output_base_path(self) -> Path | None:
        _, custom_output_base = self.resolve_output_base_paths()
        return custom_output_base

    def _toggle_custom_export(self, enabled: bool) -> None:
        self._custom_export_row.setVisible(enabled)
        if not enabled:
            self._custom_export_field.clear()
        self._update_export_paths()

    def _browse_custom_export_dir(self) -> None:
        base_dir = str(get_edit_path())
        selection = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Additional Export Folder", base_dir
        )
        if selection:
            self._custom_export_field.setText(selection)

    def _custom_export_dir(self) -> Path | None:
        text = self._custom_export_field.text().strip()
        if not text:
            return None
        # expanduser() doesn’t really throw for normal strings, but keep it simple.
        return Path(text).expanduser()

    def _update_export_paths(self) -> None:
        output_base, _ = self.resolve_output_base_paths()
        if output_base is None:
            self._export_path_label.setText("No shot code available.")
        else:
            self._export_path_label.setText(str(output_base))
