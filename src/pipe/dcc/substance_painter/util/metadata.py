"""Work out which asset and geometry variant a Substance Painter project is for.

The project file's location decides.  A tag saved in the project's metadata
names the asset and variant for copies and for files outside the asset's folder.
"""

from __future__ import annotations

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
from pipe.core.versioning import VersionStreamSpec
from pipe.core.shotgrid import Asset, ShotGrid, ShotGridError, ShotGridNotFound
from pipe.dcc.substance_painter.util.project import current_project_path

log = logging.getLogger(__name__)

PIPE_SP_METADATA_CONTEXT = "skd_asset_pipeline"
"""Substance Painter metadata context key for the asset pipeline."""

PIPE_SP_METADATA_KEY = "asset_selection"
"""Key within the metadata context that stores the tag."""

# Keys of the tag.  Projects tagged before the tag was trimmed carry more keys;
# these three are the only ones read.
TAG_ASSET_ID = "asset_id"
TAG_ASSET_PATH = "asset_path"
TAG_GEO_VARIANT = "geo_variant"

NO_ACTIVE_ASSET_MESSAGE = (
    "Could not tell which asset this project belongs to.\n\n"
    "To keep working in this project, use Open Asset → Create Asset Project → "
    "Use Currently Open Project to save it as an asset's project. Otherwise use "
    "Open Asset to open the asset's own project."
)

_SHOTGRID_LOOKUP_FAILED_MESSAGE = (
    "Could not look up this project's asset in ShotGrid. "
    "Check your network connection and try again."
)


@dataclass(frozen=True)
class ProjectIdentity:
    """The asset and geometry variant the open project file belongs to."""

    asset: Asset
    project_path: Path
    variant: str | None
    """None when the file is no listed variant's project and its tag names none."""
    is_working_file: bool
    """False for a copy of the variant's project saved under another name."""


def note_with_source(identity: ProjectIdentity, note: str | None) -> str | None:
    """Return a version's *note*, led by a line naming the file when it is a copy.

    Version history lists a copy's versions beside the working file's own,
    so that line is what tells them apart.
    """
    if identity.is_working_file:
        return note
    relative = production_relative_path(identity.project_path)
    saved_from = f"Saved from {relative or identity.project_path}."
    return f"{saved_from}\n{note}" if note else saved_from


def resolve_project_identity(conn: ShotGrid) -> ProjectIdentity | None:
    """Return the open project's asset and variant, or None if it has no asset.

    The file's location decides.  The tag only speaks for copies and for files
    outside the asset's folder, so a stale tag cannot redirect a working file.
    """
    project_path = current_project_path()
    if project_path is None:
        return None

    tag = read_tag()
    asset = _asset_at(conn, project_path.parent) or _asset_from_tag(conn, tag)
    if asset is None:
        return None

    variant = _listed_variant_at(asset, project_path)
    if variant is None and _is_tagged_for(tag, asset):
        variant = str(tag.get(TAG_GEO_VARIANT) or "").strip() or None

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
    return (
        tag.get(TAG_ASSET_ID) == asset.id or tag.get(TAG_ASSET_PATH) == asset.asset_path
    )


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


def _asset_from_tag(conn: ShotGrid, tag: dict[str, Any]) -> Asset | None:
    """Return the asset the tag names, by id and then by path, or None."""
    asset_id, asset_path = tag.get(TAG_ASSET_ID), tag.get(TAG_ASSET_PATH)
    if asset_id:
        try:
            return conn.get_asset(id=asset_id)
        except ShotGridNotFound as exc:
            log.warning(f"The tag's asset id was not found: {exc}")
    if asset_path:
        try:
            return conn.get_asset(path=asset_path)
        except ShotGridNotFound as exc:
            log.warning(f"The tag's asset path was not found: {exc}")
    return None


def read_tag() -> dict[str, Any]:
    """Return the open project's tag, or an empty dict when it has none."""
    if not sp.project.is_open():
        return {}
    try:
        tag = sp.project.Metadata(PIPE_SP_METADATA_CONTEXT).get(PIPE_SP_METADATA_KEY)
    except (ProjectError, ServiceNotFoundError):
        return {}
    return tag if isinstance(tag, dict) else {}


def tag_project(asset: Asset, geo_variant: str) -> None:
    """Record *asset* and *geo_variant* in the open project's tag."""
    write_tag(
        {
            TAG_ASSET_ID: asset.id,
            TAG_ASSET_PATH: asset.asset_path,
            TAG_GEO_VARIANT: geo_variant,
        }
    )
    log.info(f"Tagged project with {asset.asset_path} (variant={geo_variant})")


def write_tag(tag: dict[str, Any]) -> None:
    """Replace the open project's tag, e.g. to put back the one it had."""
    sp.project.Metadata(PIPE_SP_METADATA_CONTEXT).set(PIPE_SP_METADATA_KEY, tag)
