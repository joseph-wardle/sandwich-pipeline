"""Where a previs shot's RLO scene lives, and whether it has been broken out yet.

Spelled out from a sticky code rather than reused from `maya_rlo_stream`, which
needs a ShotGrid `Shot` the panel would have to fetch.
"""

from __future__ import annotations

from pathlib import Path

from pipe.core.shotgrid.paths import build_shot_path

from .state import PrevisShot


def rlo_path(shot_code: str, prod_root: Path) -> Path:
    return prod_root / build_shot_path(shot_code) / "rlo" / f"{shot_code}.mb"


def is_broken_out(shot: PrevisShot, prod_root: Path) -> bool:
    """True once an RLO scene exists on disk for this shot's sticky code.

    Says nothing about whether it still matches the live previs scene.
    """
    return bool(shot.code) and rlo_path(shot.code, prod_root).exists()
