from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from shared.util import get_production_path

from pipe.glui.dialogs import MessageDialog

if TYPE_CHECKING:
    from typing import Any

import maya.cmds as mc

from .publisher import Publisher
from .usdchaser import ChaserMode, ExportChaser

log = logging.getLogger(__name__)

CACHE_SET = "cache_SET"
PROP_SET = "prop_SET"
RIG_SET = "rig_SET"


class RigPublisher(Publisher):
    def __init__(self) -> None:
        super().__init__(use_sg_entity=False)

    def _get_entity_list(self) -> list[str]:
        return self._conn.get_asset_display_name_list_by_type(
            ["Character", "Rigged Prop"]
        )

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        kwargs = {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [(ExportChaser.ID, "mode", ChaserMode.CHAR)],
            "exportCollectionBasedBindings": True,
            "exportMaterialCollections": True,
            "legacyMaterialScope": True,
            "materialCollectionsPath": "/ROOT/MODEL",
            "shadingMode": "useRegistry",
        }

        return kwargs

    def _presave(self) -> bool:
        mc.select(CACHE_SET)
        return True

    def _get_save_path(self) -> Path | None:
        asset = self._conn.get_asset_by_display_name(self._selected_item)

        try:
            assert asset.asset_path
        except AssertionError:
            error = MessageDialog(
                self._window,
                "Error: Could not resolve the location for this asset in ShotGrid. Nothing exported",
                "Error",
            )
            error.exec_()
            return None

        asset_dir = get_production_path() / asset.asset_path
        asset_dir.mkdir(parents=True, exist_ok=True)  # Ensure the directory exists
        filename = asset.name + ".usd"
        save_path = asset_dir / filename

        if save_path.exists():
            # Look for existing versioned files
            existing_versions = [
                int(match.group(1))
                for file in asset_dir.glob(f"{asset.name}_v*.usd")
                if (
                    match := re.match(
                        rf"{re.escape(asset.name)}_v(\d+)\.usd", file.name
                    )
                )
            ]
            next_version = max(existing_versions, default=0) + 1
            versioned_name = f"{asset.name}_v{next_version}.usd"
            versioned_path = asset_dir / versioned_name

            # Move the old file to the versioned name
            shutil.move(save_path, versioned_path)

        return save_path
