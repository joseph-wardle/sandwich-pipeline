"""Canonical asset path conventions for the pipeline.

This module is the single source of truth for asset layout and naming.
Asset location is derived from ShotGrid asset metadata:
asset/<optional-subdirectory>/<asset-name>.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core.util.util import get_production_path

from core.shotgrid import Asset
from core.versioning.model import DCC_HOUDINI, DCC_MAYA, DCC_SUBSTANCE
from core.versioning.store import VERSION_MANIFEST_FILENAME

log = logging.getLogger(__name__)

# Asset root-level filenames
MODEL_FILENAME = "model.mb"
BLENDER_MODEL_FILENAME = "model.blend"
TEXTURES_FILENAME = "textures.spp"
TEXTURES_VARIANT_TEMPLATE = "textures.{variant}.spp"
ASSET_BUILDER_FILENAME = "asset_builder.hipnc"
MANIFEST_FILENAME = VERSION_MANIFEST_FILENAME

# Directory names
BACKUP_DIRNAME = ".backup"
PUBLISH_DIRNAME = "publish"
PUBLISH_SOURCE_DIRNAME = "_src"
PUBLISH_TEXTURES_DIRNAME = "tex"
PUBLISH_TEXTURES_SOURCE_DIRNAME = "_src"
PUBLISH_TEXTURES_PREVIEW_DIRNAME = "_preview"
RIG_DIRNAME = "rig"
RIG_VERSIONS_DIRNAME = ".versions"

# Publish filenames
PUBLISH_SOURCE_MODEL_FILENAME = "model.usd"
PUBLISH_ASSET_USD_FILENAME = "asset.usd"
PUBLISH_GEO_USD_FILENAME = "geo.usd"
PUBLISH_MTL_USD_FILENAME = "mtl.usd"
PUBLISH_PAYLOAD_USD_FILENAME = "payload.usd"

# Texture naming rule: <material>.<variant>.<map>.<udim>.<ext>
TEXTURE_NAME_TEMPLATE = "{material}.{variant}.{map}.{udim}"


def asset_root_from_path(
    asset_path: str | Path, production_root: Path | None = None
) -> Path:
    """Resolve a ShotGrid asset path to an absolute root."""
    root = Path(asset_path)
    if root.is_absolute():
        return root
    prod_root = production_root or get_production_path()
    return prod_root / root


def asset_root(
    asset: Asset,
    production_root: Path | None = None,
    fallback_name: str | None = None,
) -> Path:
    """Resolve an asset root from the canonical asset-relative path."""
    asset_path = getattr(asset, "asset_path", None)
    if asset_path:
        return asset_root_from_path(asset_path, production_root=production_root)

    fallback = (
        fallback_name
        or getattr(asset, "display_name", None)
        or getattr(asset, "name", None)
        or "asset"
    )
    log.warning("Asset location metadata missing; falling back to %s", fallback)
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

    def publish_textures_layer_dir(
        self, geo: str, mat: str, material_layer: str
    ) -> Path:
        """Return publish/tex/<geo>/<material>/<material_layer>."""
        return (
            self.publish_textures_dir
            / geo.strip()
            / mat.strip()
            / material_layer.strip()
        )

    def publish_textures_src_dir(self, geo: str, mat: str, material_layer: str) -> Path:
        """Return publish/tex/<geo>/<material>/<material_layer>/_src."""
        return (
            self.publish_textures_layer_dir(geo, mat, material_layer)
            / PUBLISH_TEXTURES_SOURCE_DIRNAME
        )

    def publish_textures_preview_dir(
        self, geo: str, mat: str, material_layer: str
    ) -> Path:
        """Return publish/tex/<geo>/<material>/<material_layer>/_preview."""
        return (
            self.publish_textures_layer_dir(geo, mat, material_layer)
            / PUBLISH_TEXTURES_PREVIEW_DIRNAME
        )

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def model_path(self) -> Path:
        return self.root / MODEL_FILENAME

    @property
    def blender_model_path(self) -> Path:
        return self.root / BLENDER_MODEL_FILENAME

    @property
    def textures_path(self) -> Path:
        return self.root / TEXTURES_FILENAME

    def textures_variant_path(self, variant: str) -> Path:
        """Return the variant-scoped Substance project path in the asset root."""
        variant_name = variant.strip() or "main"
        return self.root / TEXTURES_VARIANT_TEMPLATE.format(variant=variant_name)

    @property
    def rig_path(self) -> Path:
        return self.publish_dir / RIG_DIRNAME

    @property
    def rig_versions_path(self) -> Path:
        return self.rig_path / RIG_VERSIONS_DIRNAME

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


def paths_for_asset(asset: Asset, production_root: Path | None = None) -> AssetPaths:
    return AssetPaths(asset_root(asset, production_root=production_root))


__all__ = [
    "AssetPaths",
    "DCC_MAYA",
    "DCC_HOUDINI",
    "DCC_SUBSTANCE",
    "MODEL_FILENAME",
    "TEXTURES_FILENAME",
    "TEXTURES_VARIANT_TEMPLATE",
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
