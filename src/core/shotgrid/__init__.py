"""ShotGrid integration — single-import surface for every pipeline caller.

The `ShotGrid` connection class lives in `core.shotgrid.client`; the
entity types in `core.shotgrid.entities`; the path helpers in
`core.shotgrid.paths`; the exception hierarchy in
`core.shotgrid.errors`.  Callers should reach for everything via this
package: `from core.shotgrid import ShotGrid, Asset, ShotGridNotFound, ...`.
"""

from __future__ import annotations

from core.shotgrid.client import SG_Config, ShotGrid
from core.shotgrid.entities import (
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
from core.shotgrid.errors import (
    ShotGridAmbiguous,
    ShotGridError,
    ShotGridNotFound,
    ShotGridWriteError,
)
from core.shotgrid.paths import (
    build_asset_path,
    build_environment_path,
    build_shot_path,
    normalize_display_name,
    normalize_subdirectory,
    validate_shot_code_token,
)

__all__ = [
    # Connection
    "SG_Config",
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
    "normalize_display_name",
    "normalize_subdirectory",
    "validate_shot_code_token",
]
