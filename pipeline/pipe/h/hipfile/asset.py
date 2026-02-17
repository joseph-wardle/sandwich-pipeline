from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou

from pipe.struct.db import Asset, SGEntity

from .. import nodelayouts
from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HAssetFileManager(HFileManager):
    def __init__(self) -> None:
        super().__init__(Asset)

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        return "asset_builder", "hipnc"

    def _post_open_file(self, entity: SGEntity) -> None:
        asset = cast(Asset, entity)
        asset_name = (
            (asset.name or "").strip()
            or (asset.display_name or "").strip()
            or (Path(asset.asset_path).name if asset.asset_path else "")
        )

        if asset_name:
            hou.setContextOption("ASSET", asset_name)
        else:
            log.warning("Unable to set ASSET context option; asset name missing")

        try:
            nodelayouts.ensure_managed_skd_component_builder()
        except Exception:
            log.exception("Failed to ensure SKD Component Builder for %s", asset_name)
