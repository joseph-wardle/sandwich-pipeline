"""The Sandwich Renderer window's dark theme.

Each panel sits on its own shade, so the eye finds the shot list, the parms,
the stage and the log without reading their titles. Panels opt in with an
object name, such as `setObjectName(SHOTS)`.

Buttons take a role as a dynamic property, set with `role(button, PRIMARY)`:
the primary action of a page is filled with the accent colour, a quiet one
is flat until hovered, and one that throws work away is red.
"""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

# Object names of the panels that get their own shade.
SHOTS = "shotsPanel"
PARMS = "parmsPanel"
STAGE = "stagePanel"
LOG = "logPanel"
HEADER = "headerBar"
CARD = "card"

# Button roles.
PRIMARY = "primary"
QUIET = "quiet"
DANGER = "danger"

# Colours other modules draw with.
TEXT = "#e4e5e9"
DIM_TEXT = "#9a9ea8"
DISABLED_TEXT = "#5d6068"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#6aa1ff"
ACCENT_PRESSED = "#3a75e0"
ACCENT_MUTED = "#27406b"
SUCCESS = "#4cc38a"
WARNING = "#e8b54a"
DANGER_COLOR = "#f0605d"
INFO = "#6cb6ff"

_WINDOW = "#1f2024"
_BASE = "#18191c"
_ALTERNATE = "#1d1e22"
_RAISED = "#2c2e34"
_RAISED_HOVER = "#363940"
_PRESSED = "#24262b"
_BORDER = "#34363d"
_BORDER_STRONG = "#454851"

_SHOTS_SHADE = "#191a1d"
_PARMS_SHADE = "#222328"
_STAGE_SHADE = "#1e1f23"
_LOG_SHADE = "#131416"
_HEADER_SHADE = "#16171a"


def _style() -> str:
    # Style sheets name images by path, so the icons are written out first.
    from pipe.core.render.app import icons

    check = icons.file("check", "#ffffff")
    down = icons.file("chevron-down", DIM_TEXT)
    up = icons.file("chevron-up", DIM_TEXT)
    closed = icons.file("chevron-right", DIM_TEXT)
    opened = icons.file("chevron-down", DIM_TEXT)
    return f"""
* {{ outline: 0; }}
QToolTip {{ color: {TEXT}; background-color: #101113; border: 1px solid {_BORDER_STRONG};
    border-radius: 4px; padding: 5px 7px; }}

QWidget#{SHOTS}, QWidget#{SHOTS} QTreeWidget {{ background-color: {_SHOTS_SHADE}; }}
QWidget#{SHOTS} QTreeWidget {{ border: none; }}
QWidget#{SHOTS} QLineEdit {{ margin: 8px; }}
QScrollArea#{PARMS}, QScrollArea#{PARMS} > QWidget > QWidget {{ background-color: {_PARMS_SHADE}; }}
QScrollArea#{PARMS} {{ border: none; }}
QWidget#{STAGE} {{ background-color: {_STAGE_SHADE}; }}
QPlainTextEdit#{LOG} {{ background-color: {_LOG_SHADE}; color: #b8bbc2; border: none;
    border-top: 1px solid {_BORDER}; }}
QWidget#{HEADER} {{ background-color: {_HEADER_SHADE}; border-bottom: 1px solid {_BORDER}; }}
QFrame#{CARD} {{ background-color: {_RAISED}; border: 1px solid {_BORDER}; border-radius: 8px; }}

/* ---- buttons ---- */
QPushButton {{ background-color: {_RAISED}; color: {TEXT}; border: 1px solid {_BORDER_STRONG};
    border-radius: 6px; padding: 5px 12px; min-height: 18px; }}
QPushButton:hover {{ background-color: {_RAISED_HOVER}; border-color: #565a64; }}
QPushButton:pressed {{ background-color: {_PRESSED}; }}
QPushButton:disabled {{ color: {DISABLED_TEXT}; background-color: #232429; border-color: {_BORDER}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton[role="{PRIMARY}"] {{ background-color: {ACCENT}; border-color: {ACCENT};
    color: #ffffff; font-weight: 600; padding: 6px 16px; }}
QPushButton[role="{PRIMARY}"]:hover {{ background-color: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[role="{PRIMARY}"]:pressed {{ background-color: {ACCENT_PRESSED}; }}
QPushButton[role="{PRIMARY}"]:disabled {{ background-color: #2a3448; border-color: #2a3448;
    color: #6f7d96; }}
QPushButton[role="{QUIET}"] {{ background-color: transparent; border-color: transparent; }}
QPushButton[role="{QUIET}"]:hover {{ background-color: {_RAISED_HOVER}; }}
QPushButton[role="{QUIET}"]:disabled {{ background-color: transparent; border-color: transparent; }}
QPushButton[role="{DANGER}"]:hover {{ background-color: #4a2323; border-color: {DANGER_COLOR};
    color: #ffd7d6; }}

QToolButton {{ background-color: {_RAISED}; color: {TEXT}; border: 1px solid {_BORDER_STRONG};
    border-radius: 6px; padding: 3px 7px; }}
QToolButton:hover {{ background-color: {_RAISED_HOVER}; border-color: #565a64; }}
QToolButton[autoRaise="true"] {{ background-color: transparent; border-color: transparent; }}
QToolButton[autoRaise="true"]:hover {{ background-color: {_RAISED_HOVER}; }}
QToolButton:pressed {{ background-color: {_PRESSED}; }}
QToolButton:checked {{ background-color: {ACCENT_MUTED}; border-color: {ACCENT}; }}
QToolButton:disabled {{ color: {DISABLED_TEXT}; }}
QToolButton::menu-indicator {{ image: none; }}

/* The top bar's pages, a segmented control. */
QToolButton[nav="true"] {{ color: {DIM_TEXT}; padding: 6px 14px; font-weight: 600;
    background-color: transparent; border-color: transparent; }}
QToolButton[nav="true"]:hover {{ color: {TEXT}; background-color: #24262b; }}
QToolButton[nav="true"]:checked {{ color: {TEXT}; background-color: {_RAISED};
    border: 1px solid {_BORDER_STRONG}; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QAbstractSpinBox {{
    background-color: {_BASE}; color: {TEXT}; border: 1px solid {_BORDER_STRONG};
    border-radius: 5px; padding: 3px 6px; min-height: 18px;
    selection-background-color: {ACCENT}; }}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover {{ border-color: #5a5e68; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{
    color: {DISABLED_TEXT}; background-color: #1c1d21; border-color: {_BORDER}; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: none; width: 20px; subcontrol-origin: padding;
    subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background-color: {_RAISED}; border: 1px solid {_BORDER_STRONG};
    selection-background-color: {ACCENT_MUTED}; padding: 2px; }}
QAbstractSpinBox {{ padding-right: 18px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; width: 16px;
    subcontrol-origin: border; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator, QGroupBox::indicator, QTreeView::indicator {{ width: 14px; height: 14px;
    border: 1px solid #5a5e68; border-radius: 4px; background-color: {_BASE}; }}
QCheckBox::indicator:hover, QTreeView::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QGroupBox::indicator:checked, QTreeView::indicator:checked {{
    background-color: {ACCENT}; border-color: {ACCENT}; image: url({check}); }}
QCheckBox::indicator:disabled {{ border-color: {_BORDER}; background-color: #1c1d21; }}
QCheckBox::indicator:checked:disabled {{ background-color: #2a3448; border-color: #2a3448; }}
QCheckBox:disabled {{ color: {DISABLED_TEXT}; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; border-top: 1px solid {_BORDER}; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {DIM_TEXT}; padding: 7px 14px 8px 12px;
    border: none; border-bottom: 2px solid transparent; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:disabled {{ color: {DISABLED_TEXT}; }}

/* ---- lists and tables ---- */
QTreeView, QListView, QTableView, QTextBrowser, QPlainTextEdit, QTextEdit {{
    background-color: {_BASE}; alternate-background-color: {_ALTERNATE};
    border: 1px solid {_BORDER}; border-radius: 6px;
    selection-background-color: {ACCENT_MUTED}; selection-color: {TEXT}; }}
QTreeView::item {{ padding: 3px 2px; }}
QTreeView::item:hover {{ background-color: #24262c; }}
QTreeView::item:selected {{ background-color: {ACCENT_MUTED}; color: {TEXT}; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:has-children:!has-siblings:closed, QTreeView::branch:closed:has-children:has-siblings {{
    image: url({closed}); }}
QTreeView::branch:open:has-children:!has-siblings, QTreeView::branch:open:has-children:has-siblings {{
    image: url({opened}); }}
QHeaderView {{ background-color: transparent; }}
QHeaderView::section {{ background-color: {_RAISED}; color: {DIM_TEXT}; font-weight: 600;
    border: none; border-right: 1px solid {_BORDER}; border-bottom: 1px solid {_BORDER};
    padding: 5px 8px; }}
QHeaderView::section:first {{ border-top-left-radius: 6px; }}

QGroupBox {{ border: 1px solid {_BORDER}; border-radius: 8px; margin-top: 14px;
    padding: 10px 6px 6px 6px; background-color: rgba(255, 255, 255, 6); }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {DIM_TEXT};
    font-weight: 600; }}

QProgressBar {{ background-color: #2a2c31; border: none; border-radius: 3px; max-height: 6px;
    text-align: center; }}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}

QMenu {{ background-color: {_RAISED}; border: 1px solid {_BORDER_STRONG}; border-radius: 6px;
    padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT_MUTED}; }}
QMenu::item:disabled {{ color: {DISABLED_TEXT}; }}
QMenu::separator {{ height: 1px; background: {_BORDER}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}

QSplitter::handle {{ background-color: {_BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background-color: {ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle {{ background-color: #42454d; border-radius: 3px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background-color: #5a5e68; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSlider::groove:horizontal {{ height: 4px; background: #34363d; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -5px 0;
    border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}

QMessageBox QLabel {{ min-width: 320px; }}
"""


def apply(app: QtWidgets.QApplication) -> None:
    """Make every window of `app` dark."""
    app.setStyle("Fusion")
    font = app.font()
    font.setPointSizeF(max(font.pointSizeF(), 9.5))
    app.setFont(font)
    palette = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, _WINDOW),
        (QtGui.QPalette.WindowText, TEXT),
        (QtGui.QPalette.Base, _BASE),
        (QtGui.QPalette.AlternateBase, _ALTERNATE),
        (QtGui.QPalette.ToolTipBase, "#101113"),
        (QtGui.QPalette.ToolTipText, TEXT),
        (QtGui.QPalette.Text, TEXT),
        (QtGui.QPalette.Button, _RAISED),
        (QtGui.QPalette.ButtonText, TEXT),
        (QtGui.QPalette.BrightText, DANGER_COLOR),
        (QtGui.QPalette.Link, INFO),
        (QtGui.QPalette.Highlight, ACCENT),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.PlaceholderText, DISABLED_TEXT),
        (QtGui.QPalette.Mid, _BORDER),
        (QtGui.QPalette.Dark, "#121315"),
    ):
        palette.setColor(role, QtGui.QColor(color))
    for role in (
        QtGui.QPalette.WindowText,
        QtGui.QPalette.Text,
        QtGui.QPalette.ButtonText,
    ):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(DISABLED_TEXT))
    palette.setColor(
        QtGui.QPalette.Disabled, QtGui.QPalette.Button, QtGui.QColor("#232429")
    )
    app.setPalette(palette)
    app.setStyleSheet(_style())


def shade(widget: QtWidgets.QWidget, panel: str) -> None:
    """Give `widget` its panel's background."""
    widget.setObjectName(panel)
    # A plain QWidget draws a style sheet's background only when told to.
    widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)


def role(button: QtWidgets.QAbstractButton, kind: str) -> None:
    """Style `button` as a PRIMARY, QUIET or DANGER action."""
    button.setProperty("role", kind)
    # A property set after the style sheet was applied needs a re-polish.
    button.style().unpolish(button)
    button.style().polish(button)
