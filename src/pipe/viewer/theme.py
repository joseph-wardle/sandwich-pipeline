"""Fusion-based dark theme so the viewer sits comfortably next to the DCCs."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_WINDOW = QColor(53, 53, 53)
_BASE = QColor(42, 42, 42)
_TEXT = QColor(220, 220, 220)
_DISABLED = QColor(128, 128, 128)
_HIGHLIGHT = QColor(82, 133, 166)


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, _WINDOW)
    palette.setColor(role.WindowText, _TEXT)
    palette.setColor(role.Base, _BASE)
    palette.setColor(role.AlternateBase, _WINDOW)
    palette.setColor(role.Text, _TEXT)
    palette.setColor(role.Button, _WINDOW)
    palette.setColor(role.ButtonText, _TEXT)
    palette.setColor(role.ToolTipBase, _BASE)
    palette.setColor(role.ToolTipText, _TEXT)
    palette.setColor(role.Highlight, _HIGHLIGHT)
    palette.setColor(role.HighlightedText, QColor(255, 255, 255))
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, role.Text, _DISABLED)
    palette.setColor(disabled, role.ButtonText, _DISABLED)
    palette.setColor(disabled, role.WindowText, _DISABLED)
    app.setPalette(palette)


__all__ = ["apply_dark_theme"]
