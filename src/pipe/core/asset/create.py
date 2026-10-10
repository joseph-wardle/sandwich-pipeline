"""The ShotGrid record used to create an asset vea our tools."""

from __future__ import annotations

from collections.abc import Collection

from pipe.core.asset.naming import New
from pipe.core.asset.paths import DEFAULT_GEOMETRY_VARIANT
from pipe.core.shotgrid import Asset, ShotGrid

ASSET_TYPE = "Set Piece"
ASSET_TAG = "SKD_04_Asset"
TASK_TEMPLATE = "SKD_asset"
RIGGED_TAG = "SKD_02_rigged_asset"
RIGGED_TASK_TEMPLATE = "SKD_riggedAsset"


def create_record(
    conn: ShotGrid,
    new: New,
    *,
    variants: Collection[str] = (DEFAULT_GEOMETRY_VARIANT,),
) -> Asset:
    """Create the ShotGrid Asset for `new`, born with exactly `variants`.

    Raises:
        ValueError: Someone took the name after `new` was judged.
        ShotGridError: ShotGrid could not be reached, or rejected the create.
    """
    return conn.create_asset(
        code=new.display_name,
        asset_type=ASSET_TYPE,
        subdirectory=new.subdirectory,
        task_template=RIGGED_TASK_TEMPLATE if new.rigged else TASK_TEMPLATE,
        tags={ASSET_TAG, RIGGED_TAG} if new.rigged else {ASSET_TAG},
        geometry_variants=variants,
    )
