"""Qt enum aliases used across the previs widgets.

The Qt.py shim exposes these at runtime, but its type stubs don't"""

from __future__ import annotations

from Qt import QtCore

ALIGN_CENTER = QtCore.Qt.AlignCenter  # type: ignore[attr-defined]

CONTROL = QtCore.Qt.ControlModifier  # type: ignore[attr-defined]
SHIFT = QtCore.Qt.ShiftModifier  # type: ignore[attr-defined]

LEFT_BUTTON = QtCore.Qt.LeftButton  # type: ignore[attr-defined]

POINTING_HAND = QtCore.Qt.PointingHandCursor  # type: ignore[attr-defined]
SIZE_HOR = QtCore.Qt.SizeHorCursor  # type: ignore[attr-defined]

SCROLL_AS_NEEDED = QtCore.Qt.ScrollBarAsNeeded  # type: ignore[attr-defined]

TRANSPARENT_FOR_MOUSE = QtCore.Qt.WA_TransparentForMouseEvents  # type: ignore[attr-defined]
STYLED_BACKGROUND = QtCore.Qt.WA_StyledBackground  # type: ignore[attr-defined]

MOVE_ACTION = QtCore.Qt.MoveAction  # type: ignore[attr-defined]

ELIDE_RIGHT = QtCore.Qt.ElideRight  # type: ignore[attr-defined]
