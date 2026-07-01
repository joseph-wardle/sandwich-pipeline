"""Per-shot break-out + cam-publish status, computed from disk + a stored hash.

Both checks are camera-only and approximate: a stored camera-animation hash
plus an existence-stat on disk, never a read of the downstream RLO/cam file.
"In sync" means "matches what previs last baked," not "matches the live file."
No ShotGrid round-trip — paths are built locally from the shot code.
"""

from __future__ import annotations

from pathlib import Path

from pipe.core.shotgrid.paths import build_shot_path

from . import cameras
from .state import PrevisShot

# Break-out state — the header dot.
RLO_NO_CODE = "no_code"  # unpaired; nothing to break out to
RLO_READY = "ready"  # paired, never broken out (nothing on disk)
RLO_DRIFTED = "drifted"  # RLO file exists but the live primary has moved since
RLO_IN_SYNC = "in_sync"  # live primary matches the last break-out

# Cam-publish state — the header pip.
CAM_ABSENT_STALE = "absent_stale"  # cam.usd missing, or present but drifted
CAM_IN_SYNC = "in_sync"  # cam.usd matches the last publish


def rlo_path(shot_code: str, prod_root: Path) -> Path:
    return prod_root / build_shot_path(shot_code) / "rlo" / f"{shot_code}.mb"


def cam_path(shot_code: str, prod_root: Path) -> Path:
    return prod_root / build_shot_path(shot_code) / "cam" / "cam.usd"


def rlo_state(shot: PrevisShot, prod_root: Path) -> str:
    if not shot.shotgrid_code:
        return RLO_NO_CODE
    if not rlo_path(shot.shotgrid_code, prod_root).exists():
        return RLO_READY
    return RLO_IN_SYNC if _live_matches(shot, shot.rlo_animation_hash) else RLO_DRIFTED


def cam_state(shot: PrevisShot, prod_root: Path) -> str:
    if not shot.shotgrid_code:
        return CAM_ABSENT_STALE
    if not cam_path(shot.shotgrid_code, prod_root).exists():
        return CAM_ABSENT_STALE
    return (
        CAM_IN_SYNC
        if _live_matches(shot, shot.cam_animation_hash)
        else CAM_ABSENT_STALE
    )


def _live_matches(shot: PrevisShot, baked_hash: str | None) -> bool:
    if not shot.primary or not baked_hash:
        return False
    return cameras.compute_animation_hash(shot.primary) == baked_hash
