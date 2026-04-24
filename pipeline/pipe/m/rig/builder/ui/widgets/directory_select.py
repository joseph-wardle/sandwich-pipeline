from Qt.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget


class DirectorySelect(QWidget):
    def __init__(self, parent=None, start_dir=""):
        super().__init__(parent)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Local Override Directory")
        self.path_edit.setText(start_dir)

        self.browse_button = QPushButton("Browse")
        self.browse_button.setMaximumHeight(18)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.path_edit)
        layout.addWidget(self.browse_button)

        self.browse_button.clicked.connect(self._choose_directory)

    def _choose_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Directory",
            self.path_edit.text() or "",
        )
        if directory:
            self.path_edit.setText(directory)

    def get_path(self) -> str:
        return self.path_edit.text()

    def set_path(self, path: str) -> None:
        self.path_edit.setText(path)
