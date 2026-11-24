"""The Sandwich Renderer window's dark theme.

Each panel sits on its own shade, so the eye finds the shot list, the parms,
the stage and the log without reading their titles. Panels opt in with an
object name, such as `setObjectName(SHOTS)`.
"""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

# Object names of the panels that get their own shade.
SHOTS = "shotsPanel"
PARMS = "parmsPanel"
STAGE = "stagePanel"
LOG = "logPanel"

_WINDOW = "#2b2d31"
_BASE = "#232428"
_ALTERNATE = "#2d2f34"
_TEXT = "#dcdde0"
_DIM_TEXT = "#7d8087"
_BUTTON = "#3a3d43"
_HIGHLIGHT = "#3d79d6"
_BORDER = "#45484f"

_SHOTS_SHADE = "#1c1d20"
_PARMS_SHADE = "#313338"
_STAGE_SHADE = "#26282c"
_LOG_SHADE = "#17181a"

_STYLE = f"""
QToolTip {{ color: {_TEXT}; background-color: #1a1b1e; border: 1px solid {_BORDER}; padding: 4px; }}
QWidget#{SHOTS}, QWidget#{SHOTS} QTreeWidget {{ background-color: {_SHOTS_SHADE}; }}
QWidget#{SHOTS} QTreeWidget {{ border: none; }}
QScrollArea#{PARMS}, QScrollArea#{PARMS} > QWidget > QWidget {{ background-color: {_PARMS_SHADE}; }}
QWidget#{STAGE} {{ background-color: {_STAGE_SHADE}; }}
QPlainTextEdit#{LOG} {{ background-color: {_LOG_SHADE}; color: #b8bbc2; border: none; }}
QTabWidget::pane {{ border: 1px solid {_BORDER}; border-top: 2px solid {_HIGHLIGHT}; }}
QTabBar::tab {{ background: {_BASE}; color: {_DIM_TEXT}; padding: 6px 16px; border: 1px solid {_BORDER}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {_PARMS_SHADE}; color: {_TEXT}; }}
QGroupBox {{ border: 1px solid {_BORDER}; border-radius: 4px; margin-top: 12px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {_TEXT}; }}
QHeaderView::section {{ background-color: {_BUTTON}; color: {_TEXT}; border: none; border-right: 1px solid {_BORDER}; padding: 3px 6px; }}
QSplitter::handle {{ background-color: #111214; }}
QSplitter::handle:hover {{ background-color: {_HIGHLIGHT}; }}
"""


def apply(app: QtWidgets.QApplication) -> None:
    """Make every window of `app` dark."""
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, _WINDOW),
        (QtGui.QPalette.WindowText, _TEXT),
        (QtGui.QPalette.Base, _BASE),
        (QtGui.QPalette.AlternateBase, _ALTERNATE),
        (QtGui.QPalette.ToolTipBase, "#1a1b1e"),
        (QtGui.QPalette.ToolTipText, _TEXT),
        (QtGui.QPalette.Text, _TEXT),
        (QtGui.QPalette.Button, _BUTTON),
        (QtGui.QPalette.ButtonText, _TEXT),
        (QtGui.QPalette.BrightText, "#ff6b6b"),
        (QtGui.QPalette.Link, "#6ea8ff"),
        (QtGui.QPalette.Highlight, _HIGHLIGHT),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.PlaceholderText, _DIM_TEXT),
    ):
        palette.setColor(role, QtGui.QColor(color))
    for role in (
        QtGui.QPalette.WindowText,
        QtGui.QPalette.Text,
        QtGui.QPalette.ButtonText,
    ):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(_DIM_TEXT))
    palette.setColor(
        QtGui.QPalette.Disabled, QtGui.QPalette.Button, QtGui.QColor("#303237")
    )
    app.setPalette(palette)
    app.setStyleSheet(_STYLE)


def shade(widget: QtWidgets.QWidget, panel: str) -> None:
    """Give `widget` its panel's background."""
    widget.setObjectName(panel)
    # A plain QWidget draws a style sheet's background only when told to.
    widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)
