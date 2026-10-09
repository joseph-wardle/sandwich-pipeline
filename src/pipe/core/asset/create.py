"""The ShotGrid record every asset made by a pipeline tool starts from."""

from __future__ import annotations

from collections.abc import Collection

from pipe.core.asset.naming import New
from pipe.core.asset.paths import DEFAULT_GEOMETRY_VARIANT
from pipe.core.shotgrid import Asset, ShotGrid

# What every asset made here is in ShotGrid. Characters are made by production.
ASSET_TYPE = "Set Piece"
ASSET_TAG = "SKD_04_Asset"
TASK_TEMPLATE = "SKD_asset"
# A rigged asset gets rigging tasks, and the tag the rig builder lists props by.
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
