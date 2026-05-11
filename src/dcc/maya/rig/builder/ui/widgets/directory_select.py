from pathlib import Path

from Qt.QtCore import Signal
from Qt.QtGui import QColor
from Qt.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget

from ..styling import (
    FAILED_COLOR,
    LOCAL_OVERRIDE_COLOR,
)


class DirectorySelect(QWidget):
    directory_changed = Signal(Path)

    def __init__(self, parent=None, label: str | None = None):
        super().__init__(parent)

        self.path_edit = QLineEdit()
        self.path_edit.setMinimumWidth(32)
        if label:
            self.path_edit.setPlaceholderText(label)
        self._set_border_color(None)

        self.path: Path | None = None

        self.browse_button = QPushButton("Browse")
        self.browse_button.setMaximumHeight(18)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.path_edit)
        layout.addWidget(self.browse_button)

        self.browse_button.clicked.connect(self._choose_directory)
        self.path_edit.textChanged.connect(self._on_text_changed)
        self.path_edit.editingFinished.connect(self._on_editing_finished)

    def _choose_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Directory",
            self.path_edit.text() or "",
        )
        self.set_path(Path(directory))

    from pathlib import Path

    def _shorten_path(self, path: Path | None) -> str:
        if not path:
            return ""
        try:
            home = Path.home()
            return f"~/{path.relative_to(home)}"
        except ValueError:
            return str(path)

    def _expand_path(self, text: str) -> Path:
        return Path(text).expanduser().resolve()

    def _set_path(self, path: Path | None):
        if self.path != path:
            self.path = path
            self.directory_changed.emit(self.path)

    def get_path(self) -> Path | None:
        return self.path

    def set_path(self, path: Path | None) -> None:
        self._set_path(path)
        self.path_edit.setText(self._shorten_path(self.path))

    def _set_border_color(self, color: QColor | None):
        self.path_edit.setStyleSheet(
            f"""
            QLineEdit {{
                border: 1px solid {color.name()};
                border-radius: 3px;
            }}
            """
            if color is not None
            else ""
        )

    def _on_text_changed(self, text: str):
        text = text.strip()
        path = self._expand_path(text) if text else None
        self._set_path(path if path and path.exists() else None)
        self._set_border_color(LOCAL_OVERRIDE_COLOR if self.path else FAILED_COLOR)

    def _on_editing_finished(self):
        if self.path:
            shortened = self._shorten_path(self.path)
            if self.path_edit.text() != shortened:
                self.path_edit.setText(shortened)
