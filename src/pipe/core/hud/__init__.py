"""Composable HUD burn-in for playblasts and dailies via ffmpeg drawtext."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

FONT_PATH = (
    Path(__file__).resolve().parent
    / "resources"
    / "fonts"
    / "LondrinaSolid-Regular.ttf"
)

# Cross-DCC labels. Department-specific labels (Pass, Camera, Asset, ...) live
# with the department code, not here.
ARTIST = "Artist"
# `TITLE` is a version record's own title; `FILE` is the scene file on disk. Previs
# says FILE because its files *are* its versions — there is no title to carry.
TITLE = "Title"
FILE = "File"
SHOT = "Shot"

_DATE_FORMAT = "%Y-%m-%d"
# What a reviewer reads as "this frame is ahead of the last save", wherever the
# HUD says it — on the version here, on the previs filename that stands in for one.
UNSAVED_SUFFIX = "*"
_FRAME_COUNTER_TEMPLATE = "Frame %{{eif:n+{start}:d}}"

# Sizes as fractions of frame height so 720p and 1080p exports look the same.
_FONTSIZE_RATIO = 0.029
_PADDING_RATIO = 0.033
_BORDER_RATIO = 0.0028
_LINE_GAP_RATIO = 0.039


@dataclass(frozen=True)
class TimedText:
    """A HUD line whose text changes across the burn. Each `(n, text)` marks
    where `text` takes over."""

    changes: tuple[tuple[int, str], ...]


def timed_text(values: Sequence[str]) -> str | TimedText:
    """Coalesce per-frame `values` (index 0 is the first burned frame) into
    changepoints. Returns a plain `str` when every frame is identical."""
    changes: list[tuple[int, str]] = [(0, values[0])]
    for index, value in enumerate(values[1:], start=1):
        if value != changes[-1][1]:
            changes.append((index, value))
    if len(changes) == 1:
        return values[0]
    return TimedText(tuple(changes))


@dataclass(frozen=True)
class HudContent:
    """`left_lines` and `right_lines` stack bottom-up: index 0 is the bottom row.
    `frame_start` enables the per-frame counter in the lower-right; `None`
    disables it. `right_lines` sit above the counter."""

    left_lines: tuple[str | TimedText, ...] = ()
    right_lines: tuple[str | TimedText, ...] = ()
    frame_start: int | None = None

    def is_empty(self) -> bool:
        return not self.left_lines and not self.right_lines and self.frame_start is None


def labeled_line(label: str, value: str | int) -> str:
    return f"{label}: {value}"


def line_shot(code: str, version: str | None = None, *, unsaved: bool = False) -> str:
    """`unsaved=True` appends `*` to mark a scene that has drifted from the
    last saved version - reviewers see it at a glance."""
    line = f"{SHOT}: {code}"
    if version:
        line += f" {version}"
        if unsaved:
            line += UNSAVED_SUFFIX
    return line


def line_date(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime(_DATE_FORMAT)


def apply_hud(
    input_chain: Any,
    content: HudContent,
    resolution: tuple[int, int],
) -> Any:
    """Return `input_chain` with drawtext filters chained for `content`.
    Empty `HudContent` is a no-op."""
    if content.is_empty():
        return input_chain

    style = _Style.for_height(resolution[1])
    chain = input_chain
    for kwargs in _iter_filter_kwargs(content, style):
        chain = chain.filter("drawtext", **kwargs)
    return chain


@dataclass(frozen=True)
class _Style:
    fontsize: int
    padding: int
    border_w: int
    line_gap: int

    @classmethod
    def for_height(cls, height: int) -> "_Style":
        return cls(
            fontsize=round(height * _FONTSIZE_RATIO),
            padding=round(height * _PADDING_RATIO),
            border_w=max(1, round(height * _BORDER_RATIO)),
            line_gap=round(height * _LINE_GAP_RATIO),
        )

    def common_kwargs(self) -> dict[str, str]:
        return {
            "fontfile": str(FONT_PATH),
            "fontsize": str(self.fontsize),
            "fontcolor": "white",
            "borderw": str(self.border_w),
            "bordercolor": "black",
        }


def _stack_y(style: _Style, rows_up: int) -> str:
    """Bottom-up row position: row 0 sits at the padding, each row up adds a gap."""
    return f"h-th-{style.padding + rows_up * style.line_gap}"


def _line_kwargs(
    line: str | TimedText, x: str, y: str, common: dict[str, str]
) -> Iterator[dict[str, str]]:
    """Yield drawtext kwargs for one HUD row."""
    if isinstance(line, str):
        yield {"text": line, "x": x, "y": y, **common}
        return
    for index, (frame, text) in enumerate(line.changes):
        if index + 1 < len(line.changes):
            enable = f"between(n,{frame},{line.changes[index + 1][0] - 1})"
        else:
            enable = f"gte(n,{frame})"
        yield {"text": text, "x": x, "y": y, "enable": enable, **common}


def _iter_filter_kwargs(content: HudContent, style: _Style) -> Iterator[dict[str, str]]:
    common = style.common_kwargs()
    left_x = str(style.padding)
    right_x = f"w-tw-{style.padding}"

    for index, line in enumerate(content.left_lines):
        yield from _line_kwargs(line, left_x, _stack_y(style, index), common)

    counter_offset = 1 if content.frame_start is not None else 0
    for index, line in enumerate(content.right_lines):
        yield from _line_kwargs(
            line, right_x, _stack_y(style, index + counter_offset), common
        )

    if content.frame_start is not None:
        # Bare `:` inside the eif expression must reach ffmpeg as `\:`.
        # ffmpeg-python's filter-arg escaper already does that rewrite,
        # so we pass the colons unescaped
        yield {
            "text": _FRAME_COUNTER_TEMPLATE.format(start=content.frame_start),
            "x": right_x,
            "y": _stack_y(style, 0),
            **common,
        }


__all__ = [
    "ARTIST",
    "FILE",
    "FONT_PATH",
    "HudContent",
    "SHOT",
    "TITLE",
    "UNSAVED_SUFFIX",
    "TimedText",
    "apply_hud",
    "labeled_line",
    "line_date",
    "line_shot",
    "timed_text",
]
