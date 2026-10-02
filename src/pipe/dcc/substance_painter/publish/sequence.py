"""The texture publish sequence: export, back up the project, rebuild in Houdini."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pipe.core.asset import paths_for_asset
from pipe.core.shotgrid import Asset, ShotGrid
from pipe.core.versioning import backup_if_changed
from pipe.dcc.substance_painter.publish.export import Exporter
from pipe.dcc.substance_painter.publish.houdini_bridge import (
    HoudiniPublishCancelled,
    HoudiniPublishError,
    run_asset_builder,
    summarize_result,
)
from pipe.dcc.substance_painter.publish.progress import (
    PublishProgressCallback,
    PublishProgressUpdate,
    PublishStage,
)
from pipe.dcc.substance_painter.publish.types import TexSetExportSettings
from pipe.dcc.substance_painter.util.docs import LOG_HINT
from pipe.dcc.substance_painter.util.metadata import (
    ProjectIdentity,
    note_with_source,
    project_version_stream,
)

log = logging.getLogger(__name__)

_HOUDINI_CANCELLED_STATUS = (
    "Houdini publish cancelled before it finished. Publish again to rebuild the asset."
)


class PublishCancelled(Exception):
    """Raised at a progress update once the artist has pressed Cancel."""

    error_code = "PUBLISH_CANCELLED"

    def __init__(self, stage: PublishStage) -> None:
        super().__init__(f"Cancelled at: {stage.value}")
        self.stage = stage


@dataclass(frozen=True)
class PublishRequest:
    asset_label: str
    export_settings: tuple[TexSetExportSettings, ...]
    geo_var: str
    mat_var: str
    material_layer: str
    save_required: bool
    version_title: str
    version_note: str | None


def register_material_selection(
    conn: ShotGrid, asset: Asset, request: PublishRequest
) -> Asset:
    """Add the request's material variant and layer to ShotGrid if new."""
    if request.mat_var not in (asset.material_variants or set()):
        log.info(f"Updating new material variant: {request.mat_var}")
        asset = conn.add_material_variant(asset, request.mat_var)

    if request.material_layer not in (asset.material_layers or set()):
        log.info(f"Updating new material layer: {request.material_layer}")
        asset = conn.add_material_layer(asset, request.material_layer)

    return asset


def publish_textures(
    asset: Asset,
    identity: ProjectIdentity,
    request: PublishRequest,
    *,
    report: PublishProgressCallback,
    is_cancelled: Callable[[], bool],
) -> tuple[bool, str]:
    """Export, back up the project, and run Houdini; return (complete, summary).

    Raises ``PublishCancelled`` or ``TextureExportError`` if the export stops early.
    """

    def report_or_cancel(update: PublishProgressUpdate) -> None:
        report(update)
        if is_cancelled():
            raise PublishCancelled(update.stage)

    report_or_cancel(
        PublishProgressUpdate(
            stage=PublishStage.PREPARING_PUBLISH,
            message="Preparing the publish configuration and enabled texture sets.",
        )
    )
    Exporter(asset).export(
        request.export_settings,
        request.mat_var,
        request.geo_var,
        request.material_layer,
        progress_callback=report_or_cancel,
    )

    # The textures are published now, so Cancel only skips the Houdini build.
    backup_ok, backup_status = _backup_project(asset, identity, request, report=report)
    houdini_ok, houdini_status = _run_houdini_publish(
        asset, request, report=report, is_cancelled=is_cancelled
    )
    return (
        backup_ok and houdini_ok,
        f"Textures successfully exported!\n{backup_status}\n{houdini_status}",
    )


def _backup_project(
    asset: Asset,
    identity: ProjectIdentity,
    request: PublishRequest,
    *,
    report: PublishProgressCallback,
) -> tuple[bool, str]:
    """Back up the open project if it changed; return (succeeded, status line)."""
    project_path = identity.project_path
    asset_paths = paths_for_asset(asset)
    # The backup follows the project's variant; the textures follow the
    # dropdown.  A file with no variant of its own joins the published one's.
    project_stream = project_version_stream(asset, identity.variant or request.geo_var)

    report(
        PublishProgressUpdate(
            stage=PublishStage.BACKING_UP_PROJECT,
            message="Saving a versioned backup of the Substance Painter project.",
        )
    )
    try:
        result = backup_if_changed(
            source_path=project_path,
            backup_dir=project_stream.backup_dir,
            manifest_path=project_stream.manifest_path,
            dcc=project_stream.dcc,
            stream_key=project_stream.stream_key,
            stem=project_stream.stem,
            ext=project_stream.ext,
            stream_label=project_stream.label,
            working_path=project_stream.working_path,
            title=request.version_title,
            publish_path=asset_paths.publish_textures_layer_dir(
                request.geo_var,
                request.mat_var,
                request.material_layer,
            ),
            context="publish",
            note=note_with_source(identity, request.version_note),
            extra={
                "geo": request.geo_var,
                "material": request.mat_var,
                "material_layer": request.material_layer,
            },
            owner=project_stream.owner,
        )
    except (OSError, ValueError):
        log.exception(
            f"Backup of {project_path} to {project_stream.backup_dir} failed."
        )
        return False, (
            f"Backup failed: the project version could not be saved. {LOG_HINT}"
        )

    if result is None:
        log.warning("Backup skipped: source file missing.")
        return True, "Backup skipped: source file missing."
    if not result.changed:
        log.info("Backup skipped: no changes detected.")
        return True, "Backup skipped: no changes detected."
    if not result.backup_path:
        log.info(f"Backup created for {project_path}")
        return True, "Backup created."
    log.info(f"Backup created at {result.backup_path}")
    version_label = (
        f"v{int(result.version):03d}"
        if result.version is not None
        else result.backup_path.name
    )
    status = f'Backup created: {version_label} "{request.version_title}"'
    if not identity.is_working_file:
        status += f" in {project_stream.label}'s history, from {project_path.name}"
    return True, status


def _run_houdini_publish(
    asset: Asset,
    request: PublishRequest,
    *,
    report: PublishProgressCallback,
    is_cancelled: Callable[[], bool],
) -> tuple[bool, str]:
    """Run the headless Houdini asset build; return (succeeded, status line)."""
    report(
        PublishProgressUpdate(
            stage=PublishStage.RUNNING_HOUDINI,
            message=(
                "Textures are published. Rebuilding the asset in Houdini; "
                "this can take a few minutes."
            ),
        )
    )
    if is_cancelled():
        return False, _HOUDINI_CANCELLED_STATUS
    # The progress dialog stays window-modal until this returns, which is
    # what stops the artist closing the project mid-build.
    try:
        result = run_asset_builder(
            asset,
            geo_variant=request.geo_var,
            is_cancelled=is_cancelled,
        )
    except HoudiniPublishCancelled:
        log.info("Headless Houdini publish cancelled from Substance.")
        return False, _HOUDINI_CANCELLED_STATUS
    except HoudiniPublishError as exc:
        log.error(f"Headless Houdini publish failed from Substance: {exc}")
        return False, f"Houdini publish failed: {exc}"
    except Exception:
        # The textures are already published, so report this step alone.
        log.exception("Unexpected error in the headless Houdini publish.")
        return False, f"Houdini publish failed unexpectedly. {LOG_HINT}"
    return True, summarize_result(result)
