"""Read and write asset metadata in Substance Painter project files.

Associates Substance Painter projects with pipeline assets by persisting
asset identity and texture-set mappings in the project's embedded metadata.
This allows export and versioning tools to know which pipeline asset a
Substance Painter project belongs to without relying on file paths alone.

Public API
----------
- get_asset_selection_metadata()
- ProjectIdentity
- resolve_project_identity()
- identify_open_project()
- project_version_stream()
- tag_project()
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import substance_painter as sp
from pipe.core.asset import (
    DEFAULT_GEO_VARIANT,
    asset_owner_for,
    paths_for_asset,
    substance_project_stream,
)
from pipe.core.util.paths import is_same_production_file, production_relative_path
from Qt import QtWidgets
from substance_painter.exception import ProjectError, ServiceNotFoundError

from pipe.core.ui import MessageDialog
from pipe.core.versioning import DCC_SUBSTANCE, VersionStreamSpec
from pipe.core.shotgrid import (
    Asset,
    ShotGrid,
    ShotGridError,
    ShotGridNotFound,
    build_asset_path,
)
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

NO_ACTIVE_ASSET_MESSAGE = (
    "Could not tell which asset this project belongs to. "
    "Use Open Asset to create or open the asset project first."
)

_SHOTGRID_LOOKUP_FAILED_MESSAGE = (
    "Could not look up this project's asset in ShotGrid. "
    "Check your network connection and try again."
)


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


# ---------------------------------------------------------------------------
# Project identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectIdentity:
    """The asset and geometry variant the open project file belongs to."""

    asset: Asset
    project_path: Path
    variant: str | None
    """None when the file is no listed variant's project and its tag names none."""
    is_working_file: bool
    """False for a copy of the variant's project saved under another name."""


def resolve_project_identity(conn: ShotGrid) -> ProjectIdentity | None:
    """Return the open project's asset and variant, or None if it has no asset.

    The file's location decides.  The tag only speaks for copies and for files
    outside the asset's folder, so a stale tag cannot redirect a working file.
    """
    project_path = current_project_path()
    if project_path is None:
        return None

    tag = get_asset_selection_metadata()
    asset = _asset_at(conn, project_path.parent) or _asset_from_tag(conn, tag)
    if asset is None:
        return None

    variant = _listed_variant_at(asset, project_path)
    if variant is None and _is_tagged_for(tag, asset):
        variant = str(tag.get("geo_variant") or "").strip() or None

    is_working_file = variant is not None and is_same_production_file(
        project_path, paths_for_asset(asset).textures_variant_path(variant)
    )
    return ProjectIdentity(asset, project_path, variant, is_working_file)


def identify_open_project(
    conn: ShotGrid, parent: QtWidgets.QWidget | None, action_name: str
) -> ProjectIdentity | None:
    """Return the open project's identity, or tell the artist why it has none."""
    try:
        identity = resolve_project_identity(conn)
    except ShotGridError:
        log.exception("Could not look up the open project's asset in ShotGrid.")
        MessageDialog(parent, _SHOTGRID_LOOKUP_FAILED_MESSAGE, action_name).exec_()
        return None
    if identity is None:
        MessageDialog(parent, NO_ACTIVE_ASSET_MESSAGE, action_name).exec_()
    return identity


def project_version_stream(asset: Asset, variant: str) -> VersionStreamSpec:
    """Return the version stream of *asset*'s project for *variant*."""
    return substance_project_stream(
        paths_for_asset(asset), variant, owner=asset_owner_for(asset)
    )


def _is_tagged_for(tag: dict[str, Any], asset: Asset) -> bool:
    return tag.get("asset_id") == asset.id or tag.get("asset_path") == asset.asset_path


def _listed_variant_at(asset: Asset, project_path: Path) -> str | None:
    """Return the variant *asset* lists whose project file is *project_path*."""
    paths = paths_for_asset(asset)
    # Compared as paths, not names: a variant listed as "Main" owns
    # textures.main.spp on Windows, where Open Asset opens that same file.
    for variant in sorted(asset.geometry_variants or {DEFAULT_GEO_VARIANT}):
        if is_same_production_file(project_path, paths.textures_variant_path(variant)):
            return variant
    return None


def _asset_at(conn: ShotGrid, folder: Path) -> Asset | None:
    """Return the asset whose folder is *folder*, or None."""
    relative = production_relative_path(folder)
    if relative is None:
        return None
    try:
        return conn.get_asset(path=relative.as_posix())
    except ShotGridNotFound:
        # Any other ShotGrid failure must not fall through to the tag.
        return None


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


def _asset_from_tag(conn: ShotGrid, selection: dict[str, Any]) -> Asset | None:
    """Resolve the asset named by the project's tag, or None.

    Tries several strategies in order: asset ID, asset path, display name,
    code name.
    """
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
        return None

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

    return None
