"""Generate cyc_checker.exr, the lookdev cyclorama's scale-reference checker.

Run with a Python that provides numpy and OpenImageIO, e.g.:

    hython resources/tex/build_cyc_checker.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio

# Ten pixels per centimeter square keeps the finest grid crisp under filtering.
PIXELS_PER_METER = 1000
HALF_METER_GREYS = (0.22, 0.16)
SUB_GRID_STEP = 0.006
SQUARES_PER_METER = (2, 10, 100)  # half meter, decimeter, centimeter


def _parity(squares_per_meter: int) -> np.ndarray:
    cell = np.arange(PIXELS_PER_METER) * squares_per_meter // PIXELS_PER_METER
    return (cell[:, None] + cell[None, :]) % 2


def build(path: Path) -> None:
    half_meter, decimeter, centimeter = (_parity(n) for n in SQUARES_PER_METER)
    grey = np.where(half_meter == 0, *HALF_METER_GREYS)
    grey += np.where(decimeter == 0, -SUB_GRID_STEP, SUB_GRID_STEP)
    grey += np.where(centimeter == 0, -SUB_GRID_STEP, SUB_GRID_STEP)
    rgb = np.repeat(grey[:, :, None], 3, axis=2).astype(np.float16)

    spec = oiio.ImageSpec(PIXELS_PER_METER, PIXELS_PER_METER, 3, oiio.HALF)
    spec.attribute("compression", "zip")
    spec.attribute("oiio:ColorSpace", "Raw")
    out = oiio.ImageOutput.create(str(path))
    if out is None or not out.open(str(path), spec) or not out.write_image(rgb):
        raise RuntimeError(f"could not write {path}: {oiio.geterror()}")
    out.close()


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "cyc_checker.exr"
    build(target)
    print(f"wrote {target}")
