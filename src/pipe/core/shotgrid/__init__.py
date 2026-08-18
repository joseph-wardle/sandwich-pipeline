"""ShotGrid integration — single-import surface for every pipeline caller.

The `ShotGrid` connection class lives in `pipe.core.shotgrid.client`; the
entity types in `pipe.core.shotgrid.entities`; the path helpers in
`pipe.core.shotgrid.paths`; the exception hierarchy in
`pipe.core.shotgrid.errors`.  Callers should reach for everything via this
package: `from pipe.core.shotgrid import ShotGrid, Asset, ShotGridNotFound, ...`.
"""

from __future__ import annotations

from pipe.core.shotgrid.client import SG_Config, SHOT_TASK_TEMPLATE, ShotGrid
from pipe.core.shotgrid.entities import (
    Asset,
    Environment,
    Playlist,
    Sequence,
    SGEntity,
    Shot,
    Task,
    User,
    Version,
)
from pipe.core.shotgrid.errors import (
    ShotGridAmbiguous,
    ShotGridError,
    ShotGridNotFound,
    ShotGridWriteError,
)
from pipe.core.shotgrid.paths import (
    build_asset_path,
    build_environment_path,
    build_shot_path,
    is_previs_shot_code,
    normalize_display_name,
    normalize_subdirectory,
    validate_shot_code_token,
)

__all__ = [
    # Connection
    "SG_Config",
    "SHOT_TASK_TEMPLATE",
    "ShotGrid",
    # Entities
    "Asset",
    "Environment",
    "Playlist",
    "SGEntity",
    "Sequence",
    "Shot",
    "Task",
    "User",
    "Version",
    # Errors
    "ShotGridAmbiguous",
    "ShotGridError",
    "ShotGridNotFound",
    "ShotGridWriteError",
    # Path helpers
    "build_asset_path",
    "build_environment_path",
    "build_shot_path",
    "is_previs_shot_code",
    "normalize_display_name",
    "normalize_subdirectory",
    "validate_shot_code_token",
]
