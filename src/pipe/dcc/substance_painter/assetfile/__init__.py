"""The SKD menu's asset project actions: Open Asset, Save Version, Version History."""

from __future__ import annotations

import logging
from pathlib import Path

import substance_painter as sp
from env_sg import DB_Config
from substance_painter.exception import ProjectError
from Qt import QtWidgets
from pipe.core.util.paths import is_same_production_file, resolve_mapped_path

from pipe.core.asset import paths_for_asset
from pipe.core.shotgrid import Asset, ShotGrid, group_assets_by_subdirectory
from pipe.core.ui import (
    RESTORE_CANCEL,
    RESTORE_SAVE_FIRST,
    MessageDialog,
    MessageDialogCustomButtons,
    prompt_restore_conflict,
)
from pipe.core.ui.save_version_dialog import SaveVersionDialog
from pipe.core.ui.version_browser import VersionBrowserWidget
from pipe.dcc.substance_painter.ui.dialogs import (
    SubstanceAssetCreateModeDialog,
    SubstanceAssetDefaultProjectDialog,
    SubstanceAssetSelectDialog,
    default_project_settings,
    project_template_path,
    resolve_default_mesh_paths,
)
from pipe.dcc.substance_painter.runtime import get_main_qt_window
from pipe.dcc.substance_painter.util.docs import LOG_HINT
from pipe.dcc.substance_painter.util.metadata import (
    ProjectIdentity,
    identify_open_project,
    note_with_source,
    project_version_stream,
    read_tag,
    tag_project,
    write_tag,
)
from pipe.dcc.substance_painter.util.project import (
    check_not_busy,
    check_project_editable,
    current_project_path,
    save_project,
)
from pipe.core.versioning import (
    VersionRecord,
    VersionStreamSpec,
    list_version_records,
    resolve_working_file_version,
    restore_version,
    restored_message,
    save_version,
    saved_message,
)

log = logging.getLogger(__name__)


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


def _confirm_version_from_copy(
    parent: QtWidgets.QWidget | None,
    identity: ProjectIdentity,
    project_stream: VersionStreamSpec,
) -> bool:
    label = project_stream.label
    asset_label = identity.asset.display_name or identity.asset.name
    dialog = MessageDialogCustomButtons(
        parent,
        f"The open file, {identity.project_path.name}, isn't {asset_label}'s "
        f"working file ({label}).\n\n"
        f"If you continue, this file is saved as the next version in {label}'s\n"
        f"history. Restoring that version later replaces {label} with this file.\n\n"
        "To make this file the working file instead, use Open Asset →\n"
        "Create Asset Project → Use Currently Open Project.",
        "Save Version",
        has_cancel_button=True,
        ok_name="Continue",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _save_unsaved_changes(parent: QtWidgets.QWidget | None) -> bool:
    """Offer to save unsaved changes; False if the artist declines or it fails."""
    if not sp.project.needs_saving():
        return True
    dialog = MessageDialogCustomButtons(
        parent,
        "The project has unsaved changes. Save them before creating the version?",
        "Save Required",
        has_cancel_button=True,
        ok_name="Save",
        cancel_name="Cancel",
    )
    if not dialog.exec_():
        return False
    return save_project(
        lambda message, title: MessageDialog(parent, message, title).exec_()
    )


def _versioned_project(
    parent: QtWidgets.QWidget | None, action_name: str
) -> tuple[ProjectIdentity, VersionStreamSpec] | None:
    """Return the open project's identity and its variant's version stream.

    Returns None, after telling the artist why, when the open file has no
    asset or variant.  The file may be a copy of the working file.
    """
    identity = identify_open_project(ShotGrid.connect(DB_Config), parent, action_name)
    if identity is None:
        return None
    if identity.variant is None:
        MessageDialog(
            parent,
            "The open file isn't linked to a geometry variant of this asset, so it "
            "has no version history.\n\nUse Open Asset → Create Asset Project → "
            "Use Currently Open Project to save it as a variant's project.",
            action_name,
        ).exec_()
        return None

    return identity, project_version_stream(identity.asset, identity.variant)


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


def _close_current_project(parent: QtWidgets.QWidget | None) -> bool:
    """Close the current project. Returns True on success."""
    try:
        sp.project.close()
    except ProjectError:
        log.exception("Failed to close the Substance Painter project.")
        MessageDialog(
            parent,
            "Failed to close the currently opened project. "
            "Resolve any pending project issues and try again.",
            "Close Project Failed",
        ).exec_()
        return False
    return True


def _open_existing_project_for_asset(
    asset: Asset, project_path: Path, *, geo_variant: str
) -> None:
    """Open the asset's existing Substance Painter project."""
    parent = get_main_qt_window()
    if not project_path.exists():
        MessageDialog(
            parent,
            "This variant has no Substance Painter project yet. Use Create Asset "
            "Project to make one.",
            "Missing Substance Painter Project",
        ).exec_()
        log.warning(f"Substance project missing at {project_path}")
        return

    cur = current_project_path()
    if cur is not None and is_same_production_file(cur, project_path):
        log.info(f"{project_path} is already open.")
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        if not _close_current_project(parent):
            return

    if not _open_existing_project(project_path, parent):
        return
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

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    previous_tag = read_tag()
    tag_project(asset, geo_variant)
    if not _save_current_project_as(project_path, parent):
        # The project is still the original file, so it keeps the original tag.
        write_tag(previous_tag)
        return
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
        if not _close_current_project(parent):
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

    def _tag_and_save() -> None:
        tag_project(asset, variant)
        if not _save_current_project_as(project_path, parent):
            return
        asset_label = asset.display_name or asset.name
        log.info(f"Created Substance project at {project_path}")
        sp.logging.info(
            f"Created default project for {asset_label} (variant={variant})"
        )

    # A heavy mesh keeps Painter busy after create() returns, and Painter
    # refuses to save while busy.  It drops the callback if the project closes.
    sp.project.execute_when_not_busy(_tag_and_save)


def launch_open_asset_textures() -> None:
    """Open or create the Substance Painter project for a selected asset."""
    parent = get_main_qt_window()
    if not check_not_busy(parent, "Open Asset"):
        return

    conn = ShotGrid.connect(DB_Config)
    # Keyed on `name`, not `display_name`: the dialog resolves its pick with
    # `get_asset(name=...)`.
    assets = group_assets_by_subdirectory(conn.find_assets(), key=lambda a: a.name)

    select_dialog = SubstanceAssetSelectDialog(parent, assets, conn)
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
    project_path = paths.textures_variant_path(geo_variant)

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

    parent = get_main_qt_window()
    if not check_project_editable(parent, "Version History"):
        return

    versioned = _versioned_project(parent, "Version History")
    if versioned is None:
        return
    identity, project_stream = versioned
    if not identity.is_working_file:
        # Restoring replaces the working file, so its history opens only from it.
        label = project_stream.label
        MessageDialog(
            parent,
            f"The open file isn't this asset's {label}, so it has no version "
            f"history of its own.\n\nUse Open Asset to open {label}, or Create "
            "Asset Project → Use Currently Open Project to save this file as it.",
            "Version History",
        ).exec_()
        return

    asset = identity.asset
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

    if selected_action == VersionBrowserWidget.ACTION_RESTORE:
        _restore_project_version(parent, selected_record, identity, project_stream)


def _has_unversioned_work(project_stream: VersionStreamSpec) -> bool:
    if sp.project.needs_saving():
        return True
    return resolve_working_file_version(project_stream) is None


def _restore_project_version(
    parent: QtWidgets.QWidget | None,
    record: VersionRecord,
    identity: ProjectIdentity,
    project_stream: VersionStreamSpec,
) -> None:
    if _has_unversioned_work(project_stream):
        choice = prompt_restore_conflict(parent)
        if choice == RESTORE_CANCEL:
            return
        if choice == RESTORE_SAVE_FIRST and not _save_named_version(
            parent, identity, project_stream
        ):
            return

    # Close the open project before restoring overwrites its file on disk.
    if sp.project.is_open() and not _close_current_project(parent):
        return

    try:
        working_path = restore_version(record, project_stream)
    except Exception:
        log.exception("Failed to restore Substance Painter version.")
        MessageDialog(
            parent,
            "Could not restore that version, so the working file is unchanged. "
            "Someone else may have it open, or the version's backup may be "
            "missing.\n\n"
            "The project was closed for the restore: use Open Asset to open it "
            f"again. {LOG_HINT}",
            "Restore Version Failed",
        ).exec_()
        return

    if not _open_existing_project(working_path, parent):
        return
    MessageDialog(
        parent,
        restored_message(record),
        "Version Restored",
    ).exec_()


def launch_save_version() -> None:
    """Save the open asset project as a new named version."""

    parent = get_main_qt_window()
    if not check_project_editable(parent, "Save Version"):
        return
    versioned = _versioned_project(parent, "Save Version")
    if versioned is None:
        return
    identity, project_stream = versioned
    if not identity.is_working_file and not _confirm_version_from_copy(
        parent, identity, project_stream
    ):
        return
    _save_named_version(parent, identity, project_stream)


def _save_named_version(
    parent: QtWidgets.QWidget | None,
    identity: ProjectIdentity,
    project_stream: VersionStreamSpec,
) -> bool:
    """Save the open project, then store it as a named version in *project_stream*.

    Returns True on success.
    """
    if not _save_unsaved_changes(parent):
        return False
    dialog = SaveVersionDialog(parent)
    if not dialog.exec_():
        return False
    try:
        record = save_version(
            identity.project_path,
            project_stream,
            title=dialog.get_title(),
            note=note_with_source(identity, dialog.get_note()),
        )
    except Exception as exc:
        log.exception("Failed to save Substance Painter version.")
        MessageDialog(
            parent,
            f"Failed to save version:\n{exc}",
            "Save Version Failed",
        ).exec_()
        return False

    MessageDialog(
        parent,
        saved_message(record),
        "Version Saved",
    ).exec_()
    return True
