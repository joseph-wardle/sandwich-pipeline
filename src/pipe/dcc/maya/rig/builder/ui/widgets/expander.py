from Qt.QtCore import QSize, Qt
from Qt.QtWidgets import QFrame, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class Expander(QFrame):
    def __init__(
        self, title: str, parent: QWidget | None = None, expanded: bool = False
    ):
        super().__init__(parent)
        self.setStyleSheet("""
            Expander {
                background: palette(midlight);
                border-radius: 2px;
            }
        """)
        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setArrowType(Qt.RightArrow)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setIconSize(QSize(8, 8))
        self.toggle_button.setStyleSheet("""
            QToolButton {
                border: none;
                background: transparent;
                padding: 2px 4px;
                font-size: 11px;
                color: palette(text);
                text-align: left;
            }
            QToolButton:hover {
                background: palette(mid);
                border-radius: 2px;
            }
            QToolButton:checked {
                color: palette(text);
            }
        """)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_button.toggled.connect(self._on_toggle)

        self._content = QFrame()
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(6, 2, 6, 6)
        self._content.setLayout(self._content_layout)

        self._on_toggle(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.toggle_button)
        root.addWidget(self._content)

    def _on_toggle(self, checked: bool):
        self._content.setVisible(checked)
        self.toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def addWidget(self, widget: QWidget):
        self._content_layout.addWidget(widget)
