"""Design tokens + stylesheet snippets for the previs panel.

Mirrors the dark palette in `design-brief-v1.html`. Centralized so a color
tweak lands in one file instead of N stylesheets.
"""

from __future__ import annotations

# --- panel chrome -----------------------------------------------------------

PANEL_BG = "#2A2A2A"
PANEL_BG_DEEP = "#1E1E1E"
PANEL_BG_HEADER = "#303030"
PANEL_BG_SOFT = "#353535"
PANEL_TEXT = "#DAD5CB"
PANEL_TEXT_DIM = "#8E867A"
PANEL_BORDER = "#444444"
PANEL_BORDER_SOFT = "#383838"

ACCENT = "#2D5566"
ACCENT_EDGE = "#4A9DB8"
ACCENT_TEXT = "#E8F4F8"

# --- shot blocks ------------------------------------------------------------

SHOT_ALT = "#3A3A3A"
SHOT_ALT_EDGE = "#555555"
SHOT_ALT_TEXT = "#C8C0B0"
SHOT_EMPTY_EDGE = "#4A4A4A"
MISSING_EDGE = "#8A5A5A"  # the take's namespace is gone from the scene
MISSING_TEXT = "#C9A0A0"
TRUNC_EDGE = "#C97D52"
TRUNC_TEXT = "#F4D4BE"

# --- break-out dot (RLO) ----------------------------------------------------

RLO_PENDING = "#6E8BA8"  # no RLO scene yet — drawn as a dashed outline
RLO_BROKEN_OUT = "#88AA70"  # an RLO scene exists on disk

# --- playhead ---------------------------------------------------------------

PLAYHEAD = "#E5484D"  # head + current-frame line spanning every track

TIER_NARROW = 40  # below: only the colored sliver; tooltip carries the info
TIER_COMPACT = 110  # below: drop name + start/end labels, keep the length pill

ROW_HEIGHT_DEFAULT = 44
ROW_HEIGHT_MIN = 32
ROW_HEIGHT_MAX = 96
PX_PER_FRAME_DEFAULT = 4
PX_PER_FRAME_MIN = 2
PX_PER_FRAME_MAX = 16

_ROW_HEIGHT_STEP = 6  # pixels of row height per wheel notch


def zoom_step(
    row_height: int, px_per_frame: int, *, vertical: bool, up: bool
) -> tuple[int, int]:
    """One wheel notch of zoom, clamped. Shared so the two views cannot drift apart."""
    step = 1 if up else -1
    if vertical:
        row_height += step * _ROW_HEIGHT_STEP
    else:
        px_per_frame += step
    return (
        max(ROW_HEIGHT_MIN, min(ROW_HEIGHT_MAX, row_height)),
        max(PX_PER_FRAME_MIN, min(PX_PER_FRAME_MAX, px_per_frame)),
    )


# --- stylesheets ------------------------------------------------------------

PANEL_ROOT = f"background: {PANEL_BG}; color: {PANEL_TEXT};"

TOP_BAR = f"""
QFrame#topBar {{
    background: {PANEL_BG_DEEP};
    border-bottom: 1px solid #000;
}}
QFrame#topBar QLabel#title {{
    color: {PANEL_TEXT};
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 1px;
}}
QFrame#topBar QLabel#info {{
    color: {PANEL_TEXT_DIM};
    font-size: 11px;
}}
"""

TOOLBAR_BUTTON = f"""
QPushButton {{
    background: {PANEL_BG};
    color: {PANEL_TEXT};
    border: 1px solid {PANEL_BORDER};
    border-radius: 2px;
    padding: 4px 12px;
    font-size: 11px;
    letter-spacing: 1px;
}}
QPushButton:hover {{ border-color: {ACCENT_EDGE}; color: {ACCENT_TEXT}; }}
QPushButton:checked {{
    background: {ACCENT};
    border-color: {ACCENT_EDGE};
    color: {ACCENT_TEXT};
}}
QPushButton:disabled {{ color: {PANEL_TEXT_DIM}; border-color: {PANEL_BORDER_SOFT}; }}
"""

TOOLBAR_CHECKBOX = f"""
QCheckBox {{
    color: {PANEL_TEXT};
    font-size: 11px;
    letter-spacing: 1px;
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 12px;
    height: 12px;
    border: 1px solid {PANEL_BORDER};
    border-radius: 2px;
    background: {PANEL_BG};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_EDGE}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT_EDGE};
    border-color: {ACCENT_EDGE};
}}
"""

SHOT_HEADER = f"""
ShotHeader {{
    background: {PANEL_BG_HEADER};
    border-right: 1px solid {PANEL_BORDER_SOFT};
    border-top: 2px solid transparent;
}}
"""

SHOT_HEADER_SELECTED = f"""
ShotHeader {{
    background: {ACCENT};
    border-right: 1px solid {PANEL_BORDER_SOFT};
    border-top: 2px solid {ACCENT_EDGE};
}}
"""

TRACK_LABEL_PRIMARY = f"""
QLabel {{
    background: {PANEL_BG_DEEP};
    color: {PANEL_TEXT};
    border-right: 1px solid {PANEL_BORDER};
    padding-left: 12px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 2px;
}}
"""

TRACK_LABEL_ALT = f"""
QLabel {{
    background: {PANEL_BG_DEEP};
    color: transparent;
    border-right: 1px solid {PANEL_BORDER};
}}
"""

# Frame-info row shared by primary and alt blocks: monospace edge labels at
# 55%/40% opacity, a slightly bolder pill in the middle for the duration.
_FRAME_LABELS_PRIMARY = """
QFrame#camBlock QLabel#startFrame,
QFrame#camBlock QLabel#endFrame {
    color: rgba(255,255,255,0.55);
    font-size: 10px;
    font-family: monospace;
    background: transparent;
}
QFrame#camBlock QLabel#lengthBadge {
    background-color: rgba(0,0,0,0.35);
    color: rgba(255,255,255,0.9);
    font-size: 10px;
    font-weight: 500;
    padding: 1px 6px;
    border-radius: 2px;
}
"""

_FRAME_LABELS_ALT = """
QFrame#camBlock QLabel#startFrame,
QFrame#camBlock QLabel#endFrame {
    color: rgba(255,255,255,0.4);
    font-size: 10px;
    font-family: monospace;
    background: transparent;
}
QFrame#camBlock QLabel#lengthBadge {
    background-color: rgba(0,0,0,0.3);
    color: rgba(255,255,255,0.75);
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 2px;
}
"""

CAM_BLOCK_PRIMARY = f"""
QFrame#camBlock {{
    background-color: {ACCENT};
    border-top: 1px solid {ACCENT_EDGE};
    border-right: 1px solid {ACCENT_EDGE};
    border-bottom: 1px solid {ACCENT_EDGE};
    border-left: 3px solid {ACCENT_EDGE};
    border-radius: 2px;
}}
QFrame#camBlock QLabel#name {{
    color: {ACCENT_TEXT};
    font-size: 12px;
    font-weight: 500;
    background: transparent;
}}
{_FRAME_LABELS_PRIMARY}
"""

# Same as the primary style, but dashed border indicates an active drop target
# while an alternate is being dragged onto it.
CAM_BLOCK_PRIMARY_DROP = f"""
QFrame#camBlock {{
    background-color: {ACCENT};
    border: 2px dashed {ACCENT_TEXT};
    border-radius: 2px;
}}
QFrame#camBlock QLabel#name {{
    color: {ACCENT_TEXT};
    font-size: 12px;
    font-weight: 500;
    background: transparent;
}}
{_FRAME_LABELS_PRIMARY}
"""

CAM_BLOCK_PRIMARY_SELECTED = f"""
QFrame#camBlock {{
    background-color: {ACCENT};
    border: 2px solid {ACCENT_TEXT};
    border-radius: 2px;
}}
QFrame#camBlock QLabel#name {{
    color: {ACCENT_TEXT};
    font-size: 12px;
    font-weight: 500;
    background: transparent;
}}
{_FRAME_LABELS_PRIMARY}
"""

CAM_BLOCK_ALT = f"""
QFrame#camBlock {{
    background-color: {SHOT_ALT};
    border: 1px solid {SHOT_ALT_EDGE};
    border-radius: 2px;
}}
QFrame#camBlock QLabel#name {{
    color: {SHOT_ALT_TEXT};
    font-size: 12px;
    background: transparent;
}}
{_FRAME_LABELS_ALT}
"""

# Alt block whose intrinsic length is longer than the shot's primary — the
# block fills the column visually but the orange right-edge gradient + thick
# burnt border + chevron in the end label all say "extends past this slot."
CAM_BLOCK_ALT_TRUNC = f"""
QFrame#camBlock {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {SHOT_ALT},
        stop:0.7 {SHOT_ALT},
        stop:0.95 rgba(201, 125, 82, 0.35),
        stop:1 rgba(201, 125, 82, 0.5)
    );
    border-top: 1px solid {SHOT_ALT_EDGE};
    border-bottom: 1px solid {SHOT_ALT_EDGE};
    border-left: 1px solid {SHOT_ALT_EDGE};
    border-right: 2px solid {TRUNC_EDGE};
    border-radius: 2px;
}}
QFrame#camBlock QLabel#name {{
    color: {SHOT_ALT_TEXT};
    font-size: 12px;
    background: transparent;
}}
QFrame#camBlock QLabel#startFrame {{
    color: rgba(255,255,255,0.4);
    font-size: 10px;
    font-family: monospace;
    background: transparent;
}}
QFrame#camBlock QLabel#endFrame {{
    color: {TRUNC_TEXT};
    font-size: 10px;
    font-weight: 600;
    font-family: monospace;
    background: transparent;
}}
QFrame#camBlock QLabel#lengthBadge {{
    background-color: rgba(201, 125, 82, 0.25);
    color: {TRUNC_TEXT};
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 2px;
}}
"""

# A take whose namespace has left the scene. Hollow and dashed, matching the
# panel's other "not there yet" outline (the pending break-out dot), so absence
# reads the same way everywhere.
CAM_BLOCK_MISSING = f"""
QFrame#camBlock {{
    background-color: transparent;
    border: 1px dashed {MISSING_EDGE};
    border-radius: 2px;
}}
QFrame#camBlock QLabel#name {{
    color: {MISSING_TEXT};
    font-size: 12px;
    background: transparent;
}}
{_FRAME_LABELS_ALT}
"""

CUT_BADGE = f"""
QLabel {{
    background: rgba(0,0,0,0.35);
    color: {ACCENT_TEXT};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 0px 5px;
    border-radius: 2px;
}}
"""

EMPTY_SHOT_BLOCK = f"""
QLabel {{
    border: 1px dashed {MISSING_EDGE};
    border-radius: 2px;
    color: {MISSING_TEXT};
    font-size: 11px;
    font-style: italic;
}}
"""

ADD_ALT_CELL = f"""
QPushButton#addAlt {{
    background: transparent;
    border: 1px dashed {SHOT_EMPTY_EDGE};
    border-radius: 2px;
    color: {PANEL_TEXT_DIM};
    font-size: 11px;
    font-style: italic;
    padding: 4px 8px;
    text-align: center;
}}
QPushButton#addAlt:hover {{
    border-color: {ACCENT_EDGE};
    color: {ACCENT_EDGE};
}}
QPushButton#addAlt:disabled {{
    color: {PANEL_BORDER_SOFT};
    border-color: {PANEL_BORDER_SOFT};
}}
"""

# Resize handle stripes the right edge of a primary cam block. Always slightly
# visible so artists can see "this is grabbable"; brighter on hover/drag.
RESIZE_HANDLE_IDLE = """
QFrame#resizeHandle {
    background: rgba(74,157,184,0.20);
    border-left: 1px solid rgba(74,157,184,0.30);
}
"""

RESIZE_HANDLE_HOVER = f"""
QFrame#resizeHandle {{
    background: rgba(74,157,184,0.55);
    border-left: 1px solid {ACCENT_EDGE};
}}
"""

RESIZE_HANDLE_ACTIVE = f"""
QFrame#resizeHandle {{
    background: {ACCENT_EDGE};
    border-left: 1px solid {ACCENT_TEXT};
}}
"""

TOP_BAR_DOT = f"""
QFrame#topBarDot {{
    background: {ACCENT_EDGE};
    border-radius: 4px;
}}
"""
