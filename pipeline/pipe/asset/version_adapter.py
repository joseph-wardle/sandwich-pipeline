"""Asset-specific adapters for the shared versioning core.

This module is the single place that translates canonical asset layout rules
into :mod:`pipe.versioning` owners and stream specs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipe.struct.db import Asset
from pipe.versioning import (
    VersionOwner,
    VersionStreamSpec,
    stream_key_for,
)

from .paths import DCC_HOUDINI, DCC_MAYA, DCC_SUBSTANCE, AssetPaths


def _normalized_text(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def asset_owner_for(asset: Asset) -> VersionOwner:
    display_name = _normalized_text(asset.display_name)
    asset_name = _normalized_text(asset.name)
    asset_path = _normalized_text(asset.asset_path)
    return VersionOwner(
        kind="asset",
        code=display_name or asset_name or asset_path or "asset",
        display_name=display_name or asset_name,
        path=asset_path,
        id=asset.id,
    )


def asset_owner_from_metadata(
    *,
    display_name: str | None = None,
    asset_path: str | None = None,
    asset_id: int | None = None,
) -> VersionOwner | None:
    normalized_display_name = _normalized_text(display_name)
    normalized_asset_path = _normalized_text(asset_path)
    if (
        normalized_display_name is None
        and normalized_asset_path is None
        and asset_id is None
    ):
        return None
    return VersionOwner(
        kind="asset",
        code=normalized_display_name or normalized_asset_path or "asset",
        display_name=normalized_display_name,
        path=normalized_asset_path,
        id=asset_id,
    )


def asset_stream(
    asset_paths: AssetPaths,
    dcc: str,
    *,
    stem: str,
    ext: str,
    owner: VersionOwner | None = None,
    working_path: Path | None = None,
) -> VersionStreamSpec:
    resolved_ext = ext.lstrip(".")
    resolved_working_path = working_path or (
        asset_paths.root / f"{stem}.{resolved_ext}"
    )
    return VersionStreamSpec(
        root_path=asset_paths.root,
        manifest_path=asset_paths.manifest_path,
        backup_dir=asset_paths.backup_dir,
        dcc=dcc,
        stem=stem,
        ext=resolved_ext,
        owner=owner,
        label=resolved_working_path.name,
        stream_key=stream_key_for(dcc, stem, resolved_ext),
        working_path=resolved_working_path,
    )


def maya_model_stream(
    asset_paths: AssetPaths,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    return asset_stream(
        asset_paths,
        DCC_MAYA,
        stem="model",
        ext="mb",
        owner=owner,
        working_path=asset_paths.model_path,
    )


def substance_project_stream(
    asset_paths: AssetPaths,
    variant: str,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    normalized_variant = _normalized_text(variant) or "main"
    return asset_stream(
        asset_paths,
        DCC_SUBSTANCE,
        stem=f"textures.{normalized_variant}",
        ext="spp",
        owner=owner,
        working_path=asset_paths.textures_variant_path(normalized_variant),
    )


def houdini_asset_builder_stream(
    asset_paths: AssetPaths,
    *,
    owner: VersionOwner | None = None,
) -> VersionStreamSpec:
    return asset_stream(
        asset_paths,
        DCC_HOUDINI,
        stem="asset_builder",
        ext="hipnc",
        owner=owner,
        working_path=asset_paths.asset_builder_path,
    )


__all__ = [
    "asset_owner_for",
    "asset_owner_from_metadata",
    "asset_stream",
    "houdini_asset_builder_stream",
    "maya_model_stream",
    "substance_project_stream",
]
