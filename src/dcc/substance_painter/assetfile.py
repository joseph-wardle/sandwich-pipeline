"""Asset project workflows for Substance Painter.

Entry points for opening, creating, and versioning Substance Painter
projects associated with pipeline assets.  These are the functions wired
to Substance Painter's shelf/menu buttons.

Entry points
------------
- launch_open_asset_textures()
- launch_version_browser_for_current_project()
- launch_save_version()
"""

from __future__ import annotations

import logging
from pathlib import Path

import substance_painter as sp
from env_sg import DB_Config
from substance_painter.exception import ProjectError, ServiceNotFoundError
from Qt import QtWidgets
from core.util.util import resolve_mapped_path

from core.asset.paths import paths_for_asset
from core.asset.version_adapter import asset_owner_for, substance_project_stream
from core.shotgrid import Asset, ShotGrid
from core.glui.dialogs import MessageDialog, MessageDialogCustomButtons
from core.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from core.glui.version_browser import VersionBrowserWidget
from dcc.substance_painter.dialogs import (
    SubstanceAssetCreateModeDialog,
    SubstanceAssetDefaultProjectDialog,
    SubstanceAssetSelectDialog,
    default_project_settings,
    project_path_for_variant,
    project_template_path,
    resolve_default_mesh_paths,
)
from dcc.substance_painter.runtime import get_main_qt_window
from dcc.substance_painter.util.metadata import (
    current_project_path,
    get_active_asset_from_project,
    get_asset_selection_metadata,
    run_when_project_editable,
    store_asset_metadata_when_ready,
)
from core.versioning import (
    list_version_records,
    promote_version,
    save_version,
    version_label,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Confirmation dialogs (thin wrappers around MessageDialogCustomButtons)
# ---------------------------------------------------------------------------


def _confirm_discard_unsaved(parent: QtWidgets.QWidget | None) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        "The current project has unsaved changes. Continue and discard them?",
        "Unsaved Changes",
        has_cancel_button=True,
        ok_name="Continue",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _confirm_overwrite_project(parent: QtWidgets.QWidget | None, path: Path) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        f"A Substance Painter project already exists at {path}. Overwrite it?",
        "Overwrite Substance Painter Project",
        has_cancel_button=True,
        ok_name="Overwrite",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


# ---------------------------------------------------------------------------
# Project state helpers
# ---------------------------------------------------------------------------


def _current_geo_variant() -> str:
    """Return the geometry variant stored in project metadata, or "main"."""
    metadata = get_asset_selection_metadata()
    variant = metadata.get("geo_variant")
    if variant is None:
        return "main"
    text = str(variant).strip()
    return text or "main"


def _ensure_project_ready_for_version_action(
    parent: QtWidgets.QWidget | None, *, action_name: str
) -> bool:
    """Return True if the project is open, loaded, and idle.

    Shows an appropriate message dialog and returns False otherwise.
    """
    if not sp.project.is_open():
        MessageDialog(
            parent,
            "No Substance Painter project is open. Open an asset project first.",
            action_name,
        ).exec_()
        return False

    if sp.project.is_busy():
        MessageDialog(
            parent,
            "Substance Painter is busy. Wait for the current operation to finish.",
            action_name,
        ).exec_()
        return False

    try:
        if not sp.project.is_in_edition_state():
            MessageDialog(
                parent,
                "The project is still loading. Wait for it to finish before continuing.",
                action_name,
            ).exec_()
            return False
    except ServiceNotFoundError:
        log.exception(f"Failed to query project edition state for {action_name}.")
        return False

    return True


def _ensure_project_saved_for_version_action(
    parent: QtWidgets.QWidget | None, *, action_name: str
) -> Path | None:
    """Ensure the project is ready and saved; return the project path or None.

    Prompts the user to save if there are unsaved changes.
    """
    if not _ensure_project_ready_for_version_action(parent, action_name=action_name):
        return None

    project_path = current_project_path()
    if project_path is None:
        MessageDialog(
            parent,
            "This project has no file path yet. Use Save As first.",
            "Save Required",
        ).exec_()
        return None

    if sp.project.needs_saving():
        dialog = MessageDialogCustomButtons(
            parent,
            f"The project has unsaved changes. Save before {action_name.lower()}?",
            "Save Required",
            has_cancel_button=True,
            ok_name="Save",
            cancel_name="Cancel",
        )
        if not dialog.exec_():
            return None
        try:
            sp.project.save()
        except ProjectError:
            log.exception(f"Failed to save project before {action_name}.")
            MessageDialog(
                parent,
                "Failed to save the current project. Resolve file issues and try again.",
                "Save Failed",
            ).exec_()
            return None

        if sp.project.needs_saving():
            MessageDialog(
                parent,
                "The project still appears unsaved. Save manually and try again.",
                "Save Required",
            ).exec_()
            return None

        project_path = current_project_path()
        if project_path is None:
            MessageDialog(
                parent,
                "Could not resolve the project path after saving.",
                "Save Failed",
            ).exec_()
            return None

    return project_path


# ---------------------------------------------------------------------------
# Low-level project operations
# ---------------------------------------------------------------------------


def _open_existing_project(path: Path, parent: QtWidgets.QWidget | None) -> bool:
    """Open a Substance Painter project file. Returns True on success."""
    resolved_path = resolve_mapped_path(path)
    try:
        sp.project.open(str(resolved_path))
    except ProjectError:
        log.exception(f"Failed to open Substance Painter project: {resolved_path}")
        MessageDialog(
            parent,
            f"Failed to open the Substance Painter project:\n{resolved_path}",
            "Open Project Failed",
        ).exec_()
        return False
    return True


def _save_current_project_as(path: Path, parent: QtWidgets.QWidget | None) -> bool:
    """Save the current project to *path*. Returns True on success."""
    resolved_path = resolve_mapped_path(path)
    try:
        sp.project.save_as(str(resolved_path))
    except ProjectError:
        log.exception(f"Failed to save Substance Painter project as: {resolved_path}")
        MessageDialog(
            parent,
            f"Failed to save the Substance Painter project:\n{resolved_path}",
            "Save Failed",
        ).exec_()
        return False
    return True


def _close_current_project(
    parent: QtWidgets.QWidget | None, *, action_context: str
) -> bool:
    """Close the current project. Returns True on success."""
    try:
        sp.project.close()
    except ProjectError:
        log.exception(
            f"Failed to close Substance Painter project before {action_context}."
        )
        MessageDialog(
            parent,
            "Failed to close the currently opened project. "
            "Resolve any pending project issues and try again.",
            "Close Project Failed",
        ).exec_()
        return False
    return True


# ---------------------------------------------------------------------------
# Asset-project workflows (composed from the pieces above)
# ---------------------------------------------------------------------------


def _open_existing_project_for_asset(
    asset: Asset, project_path: Path, *, geo_variant: str
) -> None:
    """Open an existing Substance Painter project and tag it with asset metadata."""
    parent = get_main_qt_window()
    if not project_path.exists():
        MessageDialog(
            parent,
            "No Substance Painter project exists yet. Use Save Current As or Create Default.",
            "Missing Substance Painter Project",
        ).exec_()
        log.warning(f"Substance project missing at {project_path}")
        return

    cur = current_project_path()
    if cur and cur.resolve() == project_path.resolve():
        if sp.project.needs_saving():
            if not _save_current_project_as(project_path, parent):
                return
        store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        if not _close_current_project(
            parent, action_context="opening another asset project"
        ):
            return

    if not _open_existing_project(project_path, parent):
        return
    store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
    asset_label = asset.display_name or asset.name
    log.info(
        f"Opened Substance project for asset {asset_label} (variant={geo_variant})"
    )
    sp.logging.info(f"Opened project for {asset_label} (variant={geo_variant})")


def _save_current_project_as_asset(
    asset: Asset, project_path: Path, *, geo_variant: str
) -> None:
    """Save the currently open project to the asset's variant path."""
    parent = get_main_qt_window()
    if not sp.project.is_open():
        MessageDialog(
            parent,
            "No project is currently open. Open or create a project before saving.",
            "No Project Open",
        ).exec_()
        log.warning("Save current project requested with no project open.")
        return

    cur = current_project_path()
    if cur and cur.resolve() == project_path.resolve():
        store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
        return

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    if not _save_current_project_as(project_path, parent):
        return
    store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
    log.info(f"Saved Substance project to {project_path} (variant={geo_variant})")


def _create_default_project_for_asset(
    asset: Asset,
    project_path: Path,
    *,
    use_custom_mesh: bool,
    variant: str,
    custom_mesh_path: Path | None,
) -> None:
    """Create a new project from the default template and a mesh source."""
    parent = get_main_qt_window()
    paths = paths_for_asset(asset)
    log.info(
        "Creating default Substance project for "
        f"{asset.display_name or asset.name} (variant={variant})"
    )

    mesh_path, variant_path, fallback_path = resolve_default_mesh_paths(
        paths,
        use_custom_mesh=use_custom_mesh,
        custom_mesh_path=custom_mesh_path,
        variant=variant,
    )

    if not mesh_path or not mesh_path.exists():
        if use_custom_mesh:
            message = (
                "The selected custom mesh is missing. Choose a valid mesh to proceed."
            )
        elif fallback_path and variant_path and variant_path != fallback_path:
            message = (
                "No published mesh was found for the selected variant.\n"
                f"Expected: {variant_path}\nFallback: {fallback_path}"
            )
        else:
            message = (
                "No published mesh was found for the selected variant.\n"
                f"Expected: {variant_path}"
            )
        MessageDialog(parent, message, "Missing Mesh Source").exec_()
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        if not _close_current_project(
            parent, action_context="creating a default asset project"
        ):
            return

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    template = project_template_path()
    if not template.exists():
        MessageDialog(
            parent,
            "The default Painter template is missing:\n"
            f"{template}\n"
            "Contact production to restore the template.",
            "Missing Template",
        ).exec_()
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_mesh = resolve_mapped_path(mesh_path)
    resolved_template = resolve_mapped_path(template)
    try:
        sp.project.create(
            settings=default_project_settings(),
            mesh_file_path=str(resolved_mesh),
            template_file_path=str(resolved_template),
        )
    except ProjectError:
        log.exception("Failed to create Painter project from template.")
        MessageDialog(
            parent,
            "Failed to create the project from the default template. "
            "Check the template and mesh file, then try again.",
            "Create Default",
        ).exec_()
        return

    resolved_project_path = resolve_mapped_path(project_path)

    def _finalize_save() -> None:
        if not _save_current_project_as(resolved_project_path, parent):
            return
        store_asset_metadata_when_ready(asset, geo_variant=variant)

    run_when_project_editable(_finalize_save)
    asset_label = asset.display_name or asset.name
    log.info(f"Created Substance project at {project_path}")
    sp.logging.info(f"Created default project for {asset_label} (variant={variant})")


# ---------------------------------------------------------------------------
# Public entry points (wired to Substance Painter shelf/menu)
# ---------------------------------------------------------------------------


def launch_open_asset_textures() -> None:
    """Open or create the Substance Painter project for a selected asset.

    Presents a sequence of dialogs:
    1. Select an asset and geometry variant
    2. Open existing project, or choose a creation method
    3. (If creating) Pick a mesh source and create the project
    """
    if sp.project.is_busy():
        sp.project.execute_when_not_busy(launch_open_asset_textures)
        return

    conn = ShotGrid.connect(DB_Config)
    asset_names = sorted(a.name for a in conn.find_assets())
    parent = get_main_qt_window()

    select_dialog = SubstanceAssetSelectDialog(parent, asset_names, conn)
    if not select_dialog.exec_():
        return

    asset = select_dialog.get_selected_asset()
    action = select_dialog.get_selected_action()
    geo_variant = select_dialog.get_selected_variant()
    if not action or not asset:
        return
    log.info(
        f"Open Asset: selected {asset.display_name or asset.name} "
        f"({action}, variant={geo_variant})"
    )
    paths = paths_for_asset(asset)
    project_path = project_path_for_variant(paths, geo_variant)

    if action == SubstanceAssetSelectDialog.ACTION_OPEN_EXISTING:
        _open_existing_project_for_asset(asset, project_path, geo_variant=geo_variant)
        return

    create_dialog = SubstanceAssetCreateModeDialog(parent, asset, geo_variant)
    if not create_dialog.exec_():
        return

    create_action = create_dialog.get_selected_action()
    if not create_action:
        return

    if create_action == SubstanceAssetCreateModeDialog.ACTION_USE_CURRENT:
        _save_current_project_as_asset(asset, project_path, geo_variant=geo_variant)
        return

    if create_action == SubstanceAssetCreateModeDialog.ACTION_CREATE_DEFAULT:
        default_dialog = SubstanceAssetDefaultProjectDialog(
            parent, asset, paths, geo_variant
        )
        if not default_dialog.exec_():
            return
        _create_default_project_for_asset(
            asset,
            project_path,
            use_custom_mesh=default_dialog.use_custom_mesh(),
            variant=geo_variant,
            custom_mesh_path=default_dialog.get_custom_mesh_path(),
        )


def launch_version_browser_for_current_project() -> None:
    """Show version history for the currently open asset project."""
    if sp.project.is_busy():
        sp.project.execute_when_not_busy(launch_version_browser_for_current_project)
        return

    parent = get_main_qt_window()
    if not _ensure_project_ready_for_version_action(
        parent, action_name="Version History"
    ):
        return

    conn = ShotGrid.connect(DB_Config)
    asset = get_active_asset_from_project(conn)
    if not asset:
        MessageDialog(
            parent,
            "Could not resolve the current asset from project metadata.",
            "Version History",
        ).exec_()
        return

    geo_variant = _current_geo_variant()
    asset_paths = paths_for_asset(asset)
    project_stream = substance_project_stream(
        asset_paths,
        geo_variant,
        owner=asset_owner_for(asset),
    )
    records = list_version_records(project_stream)
    if not records:
        MessageDialog(
            parent,
            "No version history was found for the current asset project.",
            "Version History",
        ).exec_()
        return

    browser = VersionBrowserWidget(
        parent,
        records,
        owner_label=asset.display_name or asset.name or "Asset",
        stream_label=project_stream.label,
    )
    if not browser.exec_():
        return

    selected_record = browser.get_selected_record()
    selected_action = browser.get_selected_action()
    if selected_record is None:
        return

    if selected_action == VersionBrowserWidget.ACTION_OPEN:
        backup_path = selected_record.backup_path
        if backup_path is None:
            MessageDialog(
                parent,
                "The selected version has no backup file path.",
                "Open Version Failed",
            ).exec_()
            return
        if not backup_path.exists() or not backup_path.is_file():
            MessageDialog(
                parent,
                f"Backup file is missing on disk:\n{backup_path}",
                "Open Version Failed",
            ).exec_()
            return

        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        if not _close_current_project(
            parent, action_context="opening a versioned project"
        ):
            return
        if not _open_existing_project(backup_path, parent):
            return
        store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
        log.info(
            f"Opened backup version {backup_path} for asset "
            f"{asset.display_name or asset.name} (variant={geo_variant})"
        )
        return

    if selected_action == VersionBrowserWidget.ACTION_PROMOTE:
        source_backup = selected_record.backup_path
        if source_backup is None or not source_backup.exists():
            MessageDialog(
                parent,
                "Cannot create a new version from this entry because the backup file is missing.",
                "Create Version Failed",
            ).exec_()
            return

        promote_dialog = PromoteVersionDialog(parent, selected_record)
        if not promote_dialog.exec_():
            return
        try:
            promoted_record = promote_version(
                selected_record,
                project_stream,
                title=promote_dialog.get_title(),
                note=promote_dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to promote Substance Painter version.")
            MessageDialog(
                parent,
                f"Failed to create new version:\n{exc}",
                "Create Version Failed",
            ).exec_()
            return

        MessageDialog(
            parent,
            (
                f"Created new version {version_label(promoted_record.version)} "
                f'"{promoted_record.title or "(untitled)"}" from the selected backup.\n'
                "Open it from Version History to continue working from it."
            ),
            "Version Created",
        ).exec_()


def launch_save_version() -> None:
    """Create a manual version for the currently open asset project."""
    if sp.project.is_busy():
        sp.project.execute_when_not_busy(launch_save_version)
        return

    parent = get_main_qt_window()
    project_path = _ensure_project_saved_for_version_action(
        parent, action_name="Save Version"
    )
    if project_path is None:
        return

    conn = ShotGrid.connect(DB_Config)
    asset = get_active_asset_from_project(conn)
    if not asset:
        MessageDialog(
            parent,
            "Could not resolve the current asset from project metadata.",
            "Save Version",
        ).exec_()
        return

    dialog = SaveVersionDialog(parent)
    if not dialog.exec_():
        return

    geo_variant = _current_geo_variant()
    project_stream = substance_project_stream(
        paths_for_asset(asset),
        geo_variant,
        owner=asset_owner_for(asset),
    )
    try:
        version_record = save_version(
            project_path,
            project_stream,
            title=dialog.get_title(),
            note=dialog.get_note(),
        )
    except Exception as exc:
        log.exception("Failed to save Substance Painter version.")
        MessageDialog(
            parent,
            f"Failed to save version:\n{exc}",
            "Save Version Failed",
        ).exec_()
        return

    MessageDialog(
        parent,
        (
            f"Saved {version_label(version_record.version)} "
            f'"{version_record.title or "(untitled)"}".'
        ),
        "Version Saved",
    ).exec_()


# ---------------------------------------------------------------------------
# Re-exports for public API stability
# ---------------------------------------------------------------------------

# These symbols were historically imported from this module by other code.
# They now live in dcc.substance_painter.util.metadata but are re-exported here so that
# existing import paths continue to work.
from dcc.substance_painter.util.metadata import (  # noqa: E402, F401
    PIPE_SP_METADATA_CONTEXT,
    PIPE_SP_METADATA_KEY,
    PIPE_SP_METADATA_SCHEMA_VERSION,
    store_asset_metadata_for_project,
    store_asset_selection_metadata,
)

__all__ = [
    "PIPE_SP_METADATA_CONTEXT",
    "PIPE_SP_METADATA_KEY",
    "PIPE_SP_METADATA_SCHEMA_VERSION",
    "get_active_asset_from_project",
    "get_asset_selection_metadata",
    "store_asset_metadata_for_project",
    "store_asset_selection_metadata",
    "launch_open_asset_textures",
    "launch_save_version",
    "launch_version_browser_for_current_project",
]
