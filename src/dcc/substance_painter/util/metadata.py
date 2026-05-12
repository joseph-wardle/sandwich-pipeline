"""Read and write asset metadata in Substance Painter project files.

Associates Substance Painter projects with pipeline assets by persisting
asset identity and texture-set mappings in the project's embedded metadata.
This allows export and versioning tools to know which pipeline asset a
Substance Painter project belongs to without relying on file paths alone.

Public API
----------
- get_asset_selection_metadata()
- store_asset_selection_metadata()
- get_active_asset_from_project()
- store_asset_metadata_for_project()
- store_asset_metadata_when_ready()

Scheduling helpers (used by other sp modules)
---------------------------------------------
- run_when_project_editable()
- run_once_on_project_edition_entered()

Utilities (used by other sp modules)
-------------------------------------
- texture_set_name()
- current_project_path()
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Callable

import substance_painter as sp
from core.util.paths import get_production_path
from substance_painter.exception import ProjectError, ServiceNotFoundError

from core.asset.paths import DCC_SUBSTANCE
from core.shotgrid import Asset, ShotGrid, build_asset_path
from dcc.substance_painter.util.texture_set import texture_set_name

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
# Shared utilities
# ---------------------------------------------------------------------------

# texture_set_name is imported from dcc.substance_painter.util.texture_set


def current_project_path() -> Path | None:
    """Return the file path of the currently open project, or None."""
    try:
        path_str = sp.project.file_path()
    except (ProjectError, ServiceNotFoundError):
        return None
    if not path_str:
        return None
    return Path(path_str)


# ---------------------------------------------------------------------------
# Project-readiness scheduling
#
# Substance Painter projects go through several states before they can be
# modified: the project must be open, in "edition" state (fully loaded), and
# not busy with another operation.  These two helpers let callers defer work
# until all three conditions are met.
# ---------------------------------------------------------------------------


def run_once_on_project_edition_entered(callback: Callable[[], None]) -> None:
    """Run *callback* the next time the project enters edition state.

    The listener disconnects itself after firing once.
    """

    def _on_edition_entered(_event: sp.event.Event) -> None:
        try:
            sp.event.DISPATCHER.disconnect(_on_edition_entered)
        except RuntimeError:
            # Already disconnected or never connected — safe to ignore.
            pass
        callback()

    sp.event.DISPATCHER.connect_strong(
        sp.event.ProjectEditionEntered, _on_edition_entered
    )


def run_when_project_editable(callback: Callable[[], None]) -> None:
    """Run *callback* as soon as the project is open, loaded, and idle.

    If any precondition is not yet met, the call is transparently deferred
    until it is.  Safe to call at any point in the project lifecycle.
    """
    if not sp.project.is_open():
        run_once_on_project_edition_entered(lambda: run_when_project_editable(callback))
        return

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(lambda: run_when_project_editable(callback))
        return

    try:
        if not sp.project.is_in_edition_state():
            run_once_on_project_edition_entered(
                lambda: run_when_project_editable(callback)
            )
            return
    except ServiceNotFoundError:
        return

    callback()


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
# Metadata write helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as a compact ISO-8601 string."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _build_asset_selection_payload(
    asset_map: dict[str, str],
    last_asset: str | None = None,
    asset_id: int | None = None,
    asset_path: str | None = None,
    asset_subdirectory: str | None = None,
    geo_variant: str | None = None,
) -> dict[str, Any]:
    """Assemble the metadata payload dict from the given fields."""
    payload: dict[str, Any] = {
        "schema_version": PIPE_SP_METADATA_SCHEMA_VERSION,
        "dcc": DCC_SUBSTANCE,
        "asset_map": asset_map,
        "updated_at": _utc_now_iso(),
    }
    if last_asset:
        payload["last_asset"] = last_asset
    if asset_id:
        payload["asset_id"] = asset_id
    if asset_path:
        payload["asset_path"] = asset_path
    if asset_subdirectory is not None:
        payload["asset_subdirectory"] = asset_subdirectory
    if geo_variant:
        payload["geo_variant"] = geo_variant
    return payload


def store_asset_selection_metadata(
    asset_map: dict[str, str],
    *,
    last_asset: str | None = None,
    asset_id: int | None = None,
    asset_path: str | None = None,
    asset_subdirectory: str | None = None,
    geo_variant: str | None = None,
) -> None:
    """Persist a texture-set-to-asset mapping in the project metadata.

    If the project is busy or not yet in edition state, the write is
    automatically deferred until it is safe.
    """
    if not sp.project.is_open():
        return

    if sp.project.is_busy():
        run_when_project_editable(
            lambda: store_asset_selection_metadata(
                asset_map,
                last_asset=last_asset,
                asset_id=asset_id,
                asset_path=asset_path,
                asset_subdirectory=asset_subdirectory,
                geo_variant=geo_variant,
            )
        )
        return

    try:
        if not sp.project.is_in_edition_state():
            run_when_project_editable(
                lambda: store_asset_selection_metadata(
                    asset_map,
                    last_asset=last_asset,
                    asset_id=asset_id,
                    asset_path=asset_path,
                    asset_subdirectory=asset_subdirectory,
                    geo_variant=geo_variant,
                )
            )
            return
    except ServiceNotFoundError:
        return

    resolved_last_asset = last_asset
    if not resolved_last_asset and asset_map:
        unique = set(asset_map.values())
        if len(unique) == 1:
            resolved_last_asset = next(iter(unique))

    payload = _build_asset_selection_payload(
        asset_map,
        last_asset=resolved_last_asset,
        asset_id=asset_id,
        asset_path=asset_path,
        asset_subdirectory=asset_subdirectory,
        geo_variant=geo_variant,
    )
    _metadata_handle().set(PIPE_SP_METADATA_KEY, payload)


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


# ---------------------------------------------------------------------------
# Convenience: store metadata for a single asset
# ---------------------------------------------------------------------------


def store_asset_metadata_for_project(
    asset: Asset, *, geo_variant: str | None = None
) -> None:
    """Map all current texture sets to a single asset and persist to metadata."""
    if not sp.project.is_open():
        return

    asset_display_name = asset.display_name or asset.code or asset.name
    if not asset_display_name:
        return

    asset_map = {
        texture_set_name(texset): asset_display_name
        for texset in sp.textureset.all_texture_sets()
    }
    store_asset_selection_metadata(
        asset_map,
        last_asset=asset_display_name,
        asset_id=asset.id,
        asset_path=asset.asset_path,
        asset_subdirectory=asset.subdirectory,
        geo_variant=geo_variant,
    )
    log.info(f"Stored asset metadata for project: {asset_display_name}")


def store_asset_metadata_when_ready(
    asset: Asset, *, geo_variant: str | None = None
) -> None:
    """Defer :func:`store_asset_metadata_for_project` until the project is editable."""
    run_when_project_editable(
        lambda: store_asset_metadata_for_project(asset, geo_variant=geo_variant)
    )
