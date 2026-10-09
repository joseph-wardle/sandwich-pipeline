"""Making a new asset: the New… flow of an Open Asset dialog, and the record it creates."""

from __future__ import annotations

import logging
from collections.abc import Collection

from Qt import QtWidgets

from pipe.core.asset.naming import Adopt, New
from pipe.core.asset.paths import DEFAULT_GEOMETRY_VARIANT, asset_root
from pipe.core.shotgrid import Asset, ShotGrid, ShotGridError
from pipe.core.ui import MessageDialog
from pipe.core.ui.new_asset_dialog import ask_new_asset

log = logging.getLogger(__name__)

TITLE = "New Asset"

# What every asset made here is in ShotGrid. Characters are made by production.
ASSET_TYPE = "Set Piece"
ASSET_TAG = "SKD_04_Asset"
TASK_TEMPLATE = "SKD_asset"
# A rigged asset gets rigging tasks, and the tag the rig builder lists props by.
RIGGED_TAG = "SKD_02_rigged_asset"
RIGGED_TASK_TEMPLATE = "SKD_riggedAsset"


def new_asset(conn: ShotGrid, parent: QtWidgets.QWidget | None) -> Asset | None:
    """Ask the artist for a new asset, then make its ShotGrid record and folder.

    Returns `None` if the artist cancels, or once they are told what went wrong.
    """
    try:
        assets = conn.find_assets()
    except ShotGridError:
        log.exception("Could not list the assets a new one must not collide with.")
        _tell(parent, "Could not reach ShotGrid. Try again, or ask a TD.")
        return None

    choice = ask_new_asset(parent, assets, title=TITLE, accept_label="Create")
    if choice is None:
        return None

    if isinstance(choice, Adopt):
        asset = choice.asset
    else:
        try:
            asset = create_record(conn, choice)
        except ValueError as exc:
            _tell(parent, f"Nothing was created. {exc}")
            return None
        except ShotGridError:
            log.exception("Could not create the asset %s.", choice.display_name)
            _tell(
                parent,
                "Could not create the asset in ShotGrid. Try again, or ask a TD.",
            )
            return None

    asset_root(asset).mkdir(mode=0o770, parents=True, exist_ok=True)
    return asset


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


def _tell(parent: QtWidgets.QWidget | None, message: str) -> None:
    MessageDialog(parent, message, TITLE).exec_()
