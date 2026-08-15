"""Layout tokens and the two semantic status colors."""

from __future__ import annotations

PAD_S = 6
PAD_M = 12
PAD_L = 18
GAP = 8

TRANSPORT_PLAY_SIZE = 46

# The destination-row status column. Fixed so the ✓/✗/… glyph cannot resize it.
STATUS_GLYPH_W = 16

OK = "#8cc88c"
FAIL = "#e08282"

OK_STYLE = f"color: {OK};"
FAIL_STYLE = f"color: {FAIL};"

__all__ = [
    "FAIL",
    "FAIL_STYLE",
    "GAP",
    "OK",
    "OK_STYLE",
    "PAD_L",
    "PAD_M",
    "PAD_S",
    "STATUS_GLYPH_W",
    "TRANSPORT_PLAY_SIZE",
]
