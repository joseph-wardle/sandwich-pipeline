"""How big each rendered frame's EXR is, and which sizes look wrong.

A frame's size follows what the camera sees, so it drifts across a shot; one
that jumps away from the frames around it usually means a broken frame, such
as one rendered without a department's layer, an empty LPE, or a write cut
short. Each frame is compared with the median of its neighbours.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

from pipe.core.render.versions import BEAUTY_PRODUCT, products

# Frames either side a frame is compared with.
NEIGHBOURS = 3
# How far from its neighbours' median a frame's size may stray, as a fraction.
TOLERANCE = 0.3
# Under this many bytes a frame holds nothing worth showing, wherever it is.
_EMPTY_BYTES = 1024


@dataclass(frozen=True)
class FrameSize:
    frame: int
    path: Path
    bytes: int
    # Denoised or raw for a denoised product, which differ in size; else "".
    kind: str
    # How much bigger (+) or smaller (−) than its neighbours, when that's
    # past TOLERANCE; else None.
    off_by: float | None = None

    @property
    def anomaly(self) -> bool:
        return self.off_by is not None


def layer_sizes(version: Path) -> list[FrameSize]:
    """The size of each frame of the version's beauty product, oldest frame first."""
    found = products(version)
    product = next(
        (p for p in found if p.name == BEAUTY_PRODUCT),
        found[0] if found else None,
    )
    if product is None:
        return []
    sizes = []
    for frame, path in product.frames.items():
        try:
            size = path.stat().st_size
        except OSError:
            # Moved by denoise between listing and reading; the next look finds it.
            continue
        sizes.append(FrameSize(frame, path, size, product.kinds.get(frame, "")))
    return flag(sizes)


def flag(sizes: list[FrameSize]) -> list[FrameSize]:
    """`sizes` with `off_by` set on those far from their neighbours of the same kind."""
    by_kind: dict[str, list[FrameSize]] = {}
    for size in sizes:
        by_kind.setdefault(size.kind, []).append(size)
    flagged: dict[int, float] = {}
    for group in by_kind.values():
        for index, size in enumerate(group):
            if size.bytes < _EMPTY_BYTES:
                flagged[size.frame] = -1.0
                continue
            around = [
                other.bytes
                for other in group[max(index - NEIGHBOURS, 0) : index + NEIGHBOURS + 1]
                if other is not size
            ]
            if len(around) < 2:
                continue
            median = statistics.median(around)
            if median and abs(size.bytes - median) / median > TOLERANCE:
                flagged[size.frame] = size.bytes / median - 1.0
    return [
        FrameSize(s.frame, s.path, s.bytes, s.kind, flagged.get(s.frame)) for s in sizes
    ]


def describe(size: FrameSize) -> str:
    """Such as "41% smaller than the frames around it"."""
    if size.off_by is None:
        return ""
    if size.off_by <= -1.0:
        return "Empty or nearly empty"
    word = "bigger" if size.off_by > 0 else "smaller"
    return f"{abs(size.off_by):.0%} {word} than the frames around it"


def human(n: int) -> str:
    """Such as "84.2 MB"."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
