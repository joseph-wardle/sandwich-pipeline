"""Pure helpers for compositing multiple camera playblasts into one grid video.

The Maya-side `MComparePlayblaster` calls into these to pick a grid shape, build
the FFmpeg `xstack` layout string, and find a system font for `drawtext`
overlays. Nothing in here touches Maya or Qt, so it stays unit-testable.
"""

from __future__ import annotations

import math
import os
from pathlib import Path


def pick_grid(camera_count: int) -> tuple[int, int]:
    """Return `(cols, rows)` for `camera_count` cells. Wider than tall — cells
    are 16:9, so columns >= rows keeps the composed video closer to 16:9."""
    if camera_count < 1:
        raise ValueError(f"camera_count must be >= 1, got {camera_count}")
    cols = math.ceil(math.sqrt(camera_count))
    rows = math.ceil(camera_count / cols)
    return cols, rows


def build_xstack_layout(cols: int, rows: int, cell_w: int, cell_h: int) -> str:
    """Build the `layout=` string for FFmpeg's `xstack` filter.

    `xstack` wants per-input pixel offsets like `0_0|w0_0|0_h0|w0_h0`. Using
    literal pixel coordinates (rather than the `w0`/`h0` symbol form) keeps the
    layout independent of input ordering and works for any (cols, rows) shape.
    """
    offsets: list[str] = []
    for row in range(rows):
        for col in range(cols):
            offsets.append(f"{col * cell_w}_{row * cell_h}")
    return "|".join(offsets)


# Stock fonts shipped with the lab Linux boxes. `drawtext` needs an actual
# `.ttf` path; it can't resolve from font family names like CSS does.
_FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def resolve_drawtext_font_path() -> str | None:
    """Return the first existing system font path, or `None` if no candidate
    exists. Callers should omit the `fontfile=` arg when this returns `None` —
    FFmpeg falls back to a built-in font."""
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file() and os.access(candidate, os.R_OK):
            return candidate
    return None


__all__ = [
    "build_xstack_layout",
    "pick_grid",
    "resolve_drawtext_font_path",
]
