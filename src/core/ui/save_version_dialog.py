from __future__ import annotations

from typing import Optional

from Qt import QtCore, QtWidgets

from core.versioning import VersionRecord, version_label

_UNTITLED_LABEL = "(untitled)"


class SaveVersionDialog(QtWidgets.QDialog):
    _layout: QtWidgets.QVBoxLayout
    _title_field: QtWidgets.QLineEdit
    _note_field: QtWidgets.QTextEdit
    _buttons: QtWidgets.QDialogButtonBox

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        title: str = "Save Version",
        prompt: str = "Create a new version.",
    ) -> None:
        super().__init__(parent)

        self.setParent(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(520, 280)

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)

        prompt_label = QtWidgets.QLabel(prompt)
        prompt_label.setWordWrap(True)
        prompt_label.setTextFormat(QtCore.Qt.PlainText)
        self._layout.addWidget(prompt_label)

        title_label = QtWidgets.QLabel("Version Title:")
        title_label.setToolTip("Required.")
        self._layout.addWidget(title_label)

        self._title_field = QtWidgets.QLineEdit()
        self._title_field.setPlaceholderText("Enter a short, meaningful title...")
        self._title_field.textChanged.connect(self._update_ok_enabled)
        self._layout.addWidget(self._title_field)

        note_label = QtWidgets.QLabel("Note (optional):")
        self._layout.addWidget(note_label)

        self._note_field = QtWidgets.QTextEdit()
        self._note_field.setPlaceholderText("Optional details about this version.")
        self._note_field.setMinimumHeight(110)
        self._layout.addWidget(self._note_field, 1)

        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        cancel_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        if ok_btn:
            ok_btn.setText("Save Version")
            ok_btn.setEnabled(False)
        if cancel_btn:
            cancel_btn.setText("Cancel")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self._layout.addWidget(self._buttons)

    def get_title(self) -> str:
        return self._title_field.text().strip()

    def get_note(self) -> Optional[str]:
        note = self._note_field.toPlainText().strip()
        return note or None

    def _update_ok_enabled(self) -> None:
        ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_btn is None:
            return
        ok_btn.setEnabled(bool(self.get_title()))


class PromoteVersionDialog(SaveVersionDialog):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        record: VersionRecord,
    ) -> None:
        version_text = version_label(record.version)
        title_text = (record.title or "").strip() or _UNTITLED_LABEL
        super().__init__(
            parent,
            title="Save as New Version",
            prompt=f"Create a new version from: {version_text} - {title_text}",
        )
        ok_btn = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setText("Create Version")
        self._title_field.setPlaceholderText("Enter title for the new version...")


__all__ = ["PromoteVersionDialog", "SaveVersionDialog"]
