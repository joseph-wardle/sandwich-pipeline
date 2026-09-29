"""Read and write asset metadata in Substance Painter project files.

Associates Substance Painter projects with pipeline assets by persisting
asset identity and texture-set mappings in the project's embedded metadata.
This allows export and versioning tools to know which pipeline asset a
Substance Painter project belongs to without relying on file paths alone.

Public API
----------
- get_asset_selection_metadata()
- get_active_asset_from_project()
- current_geo_variant()
- tag_project()
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import substance_painter as sp
from pipe.core.asset import DEFAULT_GEO_VARIANT, textures_variant_from_filename
from pipe.core.util.paths import get_production_path
from substance_painter.exception import ProjectError, ServiceNotFoundError

from pipe.core.versioning import DCC_SUBSTANCE
from pipe.core.shotgrid import Asset, ShotGrid, build_asset_path
from pipe.dcc.substance_painter.util.project import current_project_path
from pipe.dcc.substance_painter.util.texture_set import texture_set_name

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIPE_SP_METADATA_CONTEXT = "skd_asset_pipeline"
"""Substance Painter metadata context key for the asset pipeline."""

PIPE_SP_METADATA_KEY = "asset_selection"
"""Key within the metadata context that stores the asset selection payload."""

PIPE_SP_METADATA_SCHEMA_VERSION = 1
"""Schema version stamped into every metadata payload for future migration."""


# ---------------------------------------------------------------------------
# Metadata read helpers
# ---------------------------------------------------------------------------


def _metadata_handle() -> sp.project.Metadata:
    """Return a Metadata handle scoped to the asset pipeline context."""
    return sp.project.Metadata(PIPE_SP_METADATA_CONTEXT)


def _safe_get_metadata() -> dict[str, Any]:
    """Return the stored metadata dict, or an empty dict on any failure."""
    if not sp.project.is_open():
        return {}
    try:
        payload = _metadata_handle().get(PIPE_SP_METADATA_KEY)
    except (ProjectError, ServiceNotFoundError):
        return {}
    return payload if isinstance(payload, dict) else {}


def get_asset_selection_metadata() -> dict[str, Any]:
    """Return the stored asset-selection metadata for the current project.

    Returns an empty dict when no project is open or no metadata is stored.
    """
    return _safe_get_metadata()


def current_geo_variant() -> str:
    """Return the open project's geometry variant.

    Uses the project's tag, then its ``textures.<variant>.spp`` filename, then
    ``DEFAULT_GEO_VARIANT``.
    """
    tagged = str(get_asset_selection_metadata().get("geo_variant") or "").strip()
    if tagged:
        return tagged
    project_path = current_project_path()
    if project_path is not None:
        from_filename = textures_variant_from_filename(project_path.name)
        if from_filename:
            return from_filename
    return DEFAULT_GEO_VARIANT


# ---------------------------------------------------------------------------
# Metadata write
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as a compact ISO-8601 string."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def tag_project(asset: Asset, geo_variant: str) -> None:
    """Record *asset* and *geo_variant* in the open project's metadata."""
    asset_name = asset.display_name
    if not sp.project.is_open() or not asset_name:
        return

    payload: dict[str, Any] = {
        "schema_version": PIPE_SP_METADATA_SCHEMA_VERSION,
        "dcc": DCC_SUBSTANCE,
        "asset_map": {
            texture_set_name(texset): asset_name
            for texset in sp.textureset.all_texture_sets()
        },
        "last_asset": asset_name,
        "asset_id": asset.id,
        "asset_path": asset.asset_path,
        "geo_variant": geo_variant,
    }
    if asset.subdirectory is not None:
        payload["asset_subdirectory"] = asset.subdirectory

    stored = {k: v for k, v in _safe_get_metadata().items() if k != "updated_at"}
    if stored == payload:
        return
    payload["updated_at"] = _utc_now_iso()
    _metadata_handle().set(PIPE_SP_METADATA_KEY, payload)
    log.info(f"Tagged project with asset {asset_name} (variant={geo_variant})")


# ---------------------------------------------------------------------------
# Asset resolution from project metadata
# ---------------------------------------------------------------------------


def get_active_asset_from_project(conn: ShotGrid) -> Asset | None:
    """Resolve the pipeline asset associated with the current project.

    Tries several strategies in order: asset ID, asset path, display name,
    code name.  Falls back to inferring the asset from the project file path.
    Returns None when no project is open or resolution fails entirely.
    """
    if not sp.project.is_open():
        return None

    selection = get_asset_selection_metadata()
    if not selection:
        return _asset_from_project_path(conn)

    # Strategy 1: direct ID lookup
    asset_id = selection.get("asset_id")
    if asset_id:
        try:
            return conn.get_asset(id=asset_id)
        except Exception as exc:
            log.warning(f"Failed to resolve asset by id from metadata: {exc}")

    # Strategy 2: explicit asset path
    asset_path = selection.get("asset_path")
    if asset_path:
        try:
            return conn.get_asset(path=asset_path)
        except Exception as exc:
            log.warning(f"Failed to resolve asset by path from metadata: {exc}")

    asset_subdirectory = selection.get("asset_subdirectory")

    # Strategy 3: asset name from metadata
    asset_name = selection.get("last_asset")
    if not asset_name:
        asset_map = selection.get("asset_map") or {}
        unique_assets = {name for name in asset_map.values() if name}
        if len(unique_assets) == 1:
            asset_name = next(iter(unique_assets))

    if not asset_name:
        return _asset_from_project_path(conn)

    if asset_subdirectory is not None:
        try:
            return conn.get_asset(path=build_asset_path(asset_name, asset_subdirectory))
        except Exception:
            pass

    try:
        return conn.get_asset(display_name=asset_name)
    except Exception:
        pass

    try:
        return conn.get_asset(name=asset_name)
    except Exception as exc:
        log.warning(f"Failed to resolve asset from project metadata: {exc}")

    return _asset_from_project_path(conn)


def _asset_from_project_path(conn: ShotGrid) -> Asset | None:
    """Last-resort: infer the asset from the project's location on disk."""
    project_path = current_project_path()
    if not project_path:
        return None

    try:
        prod_root = get_production_path().resolve()
        project_path = project_path.resolve()
        if prod_root not in project_path.parents and project_path != prod_root:
            return None
        asset_root = project_path.parent
        rel_asset_path = asset_root.relative_to(prod_root)
    except (ValueError, OSError):
        return None

    rel_path_str = rel_asset_path.as_posix()
    try:
        return conn.get_asset(path=rel_path_str)
    except Exception as exc:
        log.warning(f"Failed to resolve asset from project path: {exc}")
        return None
