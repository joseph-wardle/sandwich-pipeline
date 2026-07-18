"""Fusion-based dark theme so the viewer sits comfortably next to the DCCs.

The QPalette handles native widget defaults; `style.stylesheet()` layers the
polish (accent buttons, hover states, styled slider) on top. Both read their
colors from `style.py`."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from pipe.viewer import style


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, QColor(style.SURFACE))
    palette.setColor(role.WindowText, QColor(style.TEXT))
    palette.setColor(role.Base, QColor(style.BASE))
    palette.setColor(role.AlternateBase, QColor(style.SURFACE))
    palette.setColor(role.Text, QColor(style.TEXT))
    palette.setColor(role.Button, QColor(style.SURFACE))
    palette.setColor(role.ButtonText, QColor(style.TEXT))
    palette.setColor(role.ToolTipBase, QColor(style.BASE))
    palette.setColor(role.ToolTipText, QColor(style.TEXT))
    palette.setColor(role.Highlight, QColor(style.ACCENT))
    palette.setColor(role.HighlightedText, QColor(style.TEXT_BRIGHT))
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, role.Text, QColor(style.DISABLED_TEXT))
    palette.setColor(disabled, role.ButtonText, QColor(style.DISABLED_TEXT))
    palette.setColor(disabled, role.WindowText, QColor(style.DISABLED_TEXT))
    app.setPalette(palette)
    app.setStyleSheet(style.stylesheet())


__all__ = ["apply_dark_theme"]
