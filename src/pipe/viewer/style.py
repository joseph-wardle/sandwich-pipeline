"""Shared style tokens for the viewer: the single place colors, spacing, and
the global stylesheet are defined so `theme.py`, `window.py`, and
`confirm_panel.py` all speak the same visual language.

Hold to `pyside6-essentials`: the stylesheet uses no image assets, so it
survives the sanitized-env deploy with nothing to bundle."""

from __future__ import annotations

from string import Template

PAD_S = 6
PAD_M = 12
PAD_L = 18
GAP = 8
RADIUS = 4

# Diameter of the round transport play button; halved for its QSS radius.
TRANSPORT_PLAY_SIZE = 46

ACCENT = "#5285a6"
ACCENT_HOVER = "#5f97bb"
ACCENT_PRESSED = "#456f8a"
ACCENT_DISABLED = "#3a4b57"
ACCENT_TEXT_DISABLED = "#9fb4c2"

OK = "#8cc88c"
FAIL = "#e08282"

SURFACE = "#353535"
RAISED = "#3f3f3f"
RAISED_HOVER = "#474747"
PRESSED = "#2f2f2f"
BASE = "#2a2a2a"
BORDER = "#454545"
BORDER_STRONG = "#5a5a5a"

TEXT = "#dcdcdc"
TEXT_BRIGHT = "#ffffff"
MUTED = "#9a9a9a"
DISABLED_TEXT = "#808080"
DISABLED_BORDER = "#3a3a3a"
DISABLED_BG = "#363636"

OK_STYLE = f"color: {OK};"
FAIL_STYLE = f"color: {FAIL};"


_STYLESHEET = Template(
    """
QToolTip {
    background-color: $base;
    color: $text;
    border: 1px solid $border_strong;
    padding: 4px 6px;
}

QPushButton {
    background-color: $raised;
    border: 1px solid $border;
    border-radius: ${radius}px;
    padding: 5px 12px;
    color: $text;
}
QPushButton:hover { background-color: $raised_hover; border-color: $border_strong; }
QPushButton:pressed { background-color: $pressed; }
QPushButton:disabled {
    color: $disabled_text;
    border-color: $disabled_border;
    background-color: $disabled_bg;
}

/* Primary/accent variant, opted into with `setProperty("primary", True)`. */
QPushButton[primary="true"] {
    background-color: $accent;
    border: 1px solid $accent;
    color: $text_bright;
    font-weight: 600;
}
QPushButton[primary="true"]:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
}
QPushButton[primary="true"]:pressed { background-color: $accent_pressed; }
QPushButton[primary="true"]:disabled {
    background-color: $accent_disabled;
    border-color: $accent_disabled;
    color: $accent_text_disabled;
}

QToolButton { border-radius: ${radius}px; padding: 2px; }
QToolButton:hover { background-color: $raised_hover; }
QToolButton:pressed { background-color: $pressed; }

/* The centered play/pause control: a round accent button, the transport's
   one primary action. Radius tracks TRANSPORT_PLAY_SIZE. */
QToolButton#transportPlay {
    background-color: $accent;
    border: 1px solid $accent;
    border-radius: ${play_radius}px;
}
QToolButton#transportPlay:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
}
QToolButton#transportPlay:pressed { background-color: $accent_pressed; }

QCheckBox { spacing: 6px; padding: 2px 0; }
QCheckBox:hover { color: $text_bright; }

QGroupBox {
    border: 1px solid $border;
    border-radius: ${radius}px;
    margin-top: 10px;
    padding: 8px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: $muted;
    font-weight: 600;
}

/* Nested well for a row's expandable sub-options (e.g. ShotGrid playlist). */
QFrame#shotgridOptions {
    background-color: $base;
    border: 1px solid $border;
    border-radius: ${radius}px;
}

QListWidget {
    border: 1px solid $border;
    border-radius: ${radius}px;
    background-color: $base;
    outline: 0;
}
QListWidget::item { padding: 6px 8px; border-radius: 3px; }
QListWidget::item:hover { background-color: $raised; }
QListWidget::item:selected { background-color: $accent; color: $text_bright; }

/* The clip filmstrip paints its own rows via a delegate, so strip the generic
   item padding and highlight that would otherwise double up beneath it. */
QListWidget#clipList::item { padding: 0; }
QListWidget#clipList::item:hover,
QListWidget#clipList::item:selected { background: transparent; }

QLineEdit {
    background-color: $base;
    border: 1px solid $border;
    border-radius: ${radius}px;
    padding: 4px 6px;
    color: $text;
}
QLineEdit:focus { border-color: $accent; }

QComboBox {
    background-color: $base;
    border: 1px solid $border;
    border-radius: ${radius}px;
    padding: 4px 6px;
    padding-right: 18px;
    color: $text;
}
QComboBox:focus { border-color: $accent; }
QComboBox:disabled { color: $disabled_text; }
QComboBox::drop-down { border: none; width: 18px; }
/* Caret drawn as a CSS border triangle so we ship no arrow image. */
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid $muted;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: $base;
    border: 1px solid $border_strong;
    selection-background-color: $accent;
    selection-color: $text_bright;
    outline: 0;
}

QSlider::groove:horizontal {
    height: 4px;
    background: $base;
    border-radius: 2px;
}
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
/* Slim vertical pill reads as a playhead marker rather than a knob. */
QSlider::handle:horizontal {
    background: $text_bright;
    width: 6px;
    margin: -6px 0;
    border-radius: 3px;
}
QSlider::handle:horizontal:hover { background: $accent_hover; }
"""
)


def stylesheet() -> str:
    """The global QSS layered over the Fusion palette in `apply_dark_theme`."""
    return _STYLESHEET.substitute(
        radius=RADIUS,
        play_radius=TRANSPORT_PLAY_SIZE // 2,
        accent=ACCENT,
        accent_hover=ACCENT_HOVER,
        accent_pressed=ACCENT_PRESSED,
        accent_disabled=ACCENT_DISABLED,
        accent_text_disabled=ACCENT_TEXT_DISABLED,
        base=BASE,
        raised=RAISED,
        raised_hover=RAISED_HOVER,
        pressed=PRESSED,
        border=BORDER,
        border_strong=BORDER_STRONG,
        text=TEXT,
        text_bright=TEXT_BRIGHT,
        muted=MUTED,
        disabled_text=DISABLED_TEXT,
        disabled_border=DISABLED_BORDER,
        disabled_bg=DISABLED_BG,
    )


__all__ = ["stylesheet"]
