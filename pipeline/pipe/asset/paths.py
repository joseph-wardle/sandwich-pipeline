"""Canonical asset path conventions for the pipeline.

This module is the single source of truth for asset layout and naming.
It respects the ShotGrid asset path for categorization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.util import get_production_path

from pipe.struct.db import Asset

log = logging.getLogger(__name__)

# DCC identifiers
DCC_MAYA = "maya"
DCC_HOUDINI = "houdini"
DCC_SUBSTANCE = "substance_painter"

# Asset root-level filenames
MODEL_FILENAME = "model.mb"
TEXTURES_FILENAME = "textures.spp"
ASSET_BUILDER_FILENAME = "asset_builder.hipnc"
MANIFEST_FILENAME = "asset_manifest.json"

# Directory names
BACKUP_DIRNAME = ".backup"
PUBLISH_DIRNAME = "publish"
PUBLISH_SOURCE_DIRNAME = "_src"
PUBLISH_TEXTURES_DIRNAME = "tex"
PUBLISH_TEXTURES_SOURCE_DIRNAME = "_src"
PUBLISH_TEXTURES_PREVIEW_DIRNAME = "_preview"

# Publish filenames
PUBLISH_SOURCE_MODEL_FILENAME = "model.usd"
PUBLISH_ASSET_USD_FILENAME = "asset.usd"
PUBLISH_GEO_USD_FILENAME = "geo.usd"
PUBLISH_MTL_USD_FILENAME = "mtl.usd"
PUBLISH_PAYLOAD_USD_FILENAME = "payload.usd"

# Texture naming rule: <material>.<variant>.<map>.<udim>.<ext>
TEXTURE_NAME_TEMPLATE = "{material}.{variant}.{map}.{udim}"


def asset_root_from_path(
    asset_path: str | Path, production_root: Optional[Path] = None
) -> Path:
    """Resolve a ShotGrid asset path to an absolute root."""
    root = Path(asset_path)
    if root.is_absolute():
        return root
    prod_root = production_root or get_production_path()
    return prod_root / root


def asset_root(
    asset: Asset,
    production_root: Optional[Path] = None,
    fallback_name: Optional[str] = None,
) -> Path:
    """Resolve an asset root while respecting the ShotGrid path when present."""
    asset_path = getattr(asset, "path", None)
    if asset_path:
        return asset_root_from_path(asset_path, production_root=production_root)

    fallback = (
        fallback_name
        or getattr(asset, "display_name", None)
        or getattr(asset, "name", None)
        or "asset"
    )
    log.warning("Asset path missing; falling back to %s", fallback)
    prod_root = production_root or get_production_path()
    return prod_root / "asset" / fallback


@dataclass(frozen=True)
class AssetPaths:
    """Convenience paths for the canonical asset layout."""

    root: Path

    @property
    def backup_dir(self) -> Path:
        return self.root / BACKUP_DIRNAME

    @property
    def publish_dir(self) -> Path:
        return self.root / PUBLISH_DIRNAME

    @property
    def publish_source_dir(self) -> Path:
        return self.publish_dir / PUBLISH_SOURCE_DIRNAME

    @property
    def publish_textures_dir(self) -> Path:
        return self.publish_dir / PUBLISH_TEXTURES_DIRNAME

    def publish_textures_layer_dir(self, geo: str, mat: str, layer: str) -> Path:
        """Return publish/tex/<geo>/<material>/<layer>."""
        return self.publish_textures_dir / geo.strip() / mat.strip() / layer.strip()

    def publish_textures_src_dir(self, geo: str, mat: str, layer: str) -> Path:
        """Return publish/tex/<geo>/<material>/<layer>/_src."""
        return (
            self.publish_textures_layer_dir(geo, mat, layer)
            / PUBLISH_TEXTURES_SOURCE_DIRNAME
        )

    def publish_textures_preview_dir(self, geo: str, mat: str, layer: str) -> Path:
        """Return publish/tex/<geo>/<material>/<layer>/_preview."""
        return (
            self.publish_textures_layer_dir(geo, mat, layer)
            / PUBLISH_TEXTURES_PREVIEW_DIRNAME
        )

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def model_path(self) -> Path:
        return self.root / MODEL_FILENAME

    @property
    def textures_path(self) -> Path:
        return self.root / TEXTURES_FILENAME

    @property
    def asset_builder_path(self) -> Path:
        return self.root / ASSET_BUILDER_FILENAME

    @property
    def publish_source_model_usd(self) -> Path:
        return self.publish_source_dir / PUBLISH_SOURCE_MODEL_FILENAME

    def publish_source_variant_usd(self, variant: str) -> Path:
        """Return the publish/_src USD path for a named variant."""
        return self.publish_source_dir / f"{variant.strip()}.usd"

    @property
    def publish_asset_usd(self) -> Path:
        return self.publish_dir / PUBLISH_ASSET_USD_FILENAME

    @property
    def publish_geo_usd(self) -> Path:
        return self.publish_dir / PUBLISH_GEO_USD_FILENAME

    @property
    def publish_mtl_usd(self) -> Path:
        return self.publish_dir / PUBLISH_MTL_USD_FILENAME

    @property
    def publish_payload_usd(self) -> Path:
        return self.publish_dir / PUBLISH_PAYLOAD_USD_FILENAME

    def texture_name(
        self, *, material: str, variant: str, map_name: str, udim: str | int
    ) -> str:
        return TEXTURE_NAME_TEMPLATE.format(
            material=material, variant=variant, map=map_name, udim=udim
        )


def paths_for_asset(asset: Asset, production_root: Optional[Path] = None) -> AssetPaths:
    return AssetPaths(asset_root(asset, production_root=production_root))


__all__ = [
    "AssetPaths",
    "DCC_MAYA",
    "DCC_HOUDINI",
    "DCC_SUBSTANCE",
    "MODEL_FILENAME",
    "TEXTURES_FILENAME",
    "ASSET_BUILDER_FILENAME",
    "MANIFEST_FILENAME",
    "BACKUP_DIRNAME",
    "PUBLISH_DIRNAME",
    "PUBLISH_SOURCE_DIRNAME",
    "PUBLISH_TEXTURES_DIRNAME",
    "PUBLISH_TEXTURES_SOURCE_DIRNAME",
    "PUBLISH_TEXTURES_PREVIEW_DIRNAME",
    "PUBLISH_SOURCE_MODEL_FILENAME",
    "PUBLISH_ASSET_USD_FILENAME",
    "PUBLISH_GEO_USD_FILENAME",
    "PUBLISH_MTL_USD_FILENAME",
    "PUBLISH_PAYLOAD_USD_FILENAME",
    "TEXTURE_NAME_TEMPLATE",
    "asset_root",
    "asset_root_from_path",
    "paths_for_asset",
]
