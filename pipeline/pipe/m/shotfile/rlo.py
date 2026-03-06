import logging
from pathlib import Path

from pipe.glui.dialogs import MessageDialog, MessageDialogCustomButtons
from pipe.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from pipe.glui.version_browser import VersionBrowserWidget
from pipe.shot.version_adapter import (
    maya_rlo_stream,
    path_matches_stream,
    shot_owner_for,
)
from pipe.struct.db import SGEntity, Shot
from pipe.versioning import (
    VersionStreamSpec,
    list_version_records,
    promote_version,
    save_version,
    version_label,
)

from .shotfile_manager import MShotFileManager

log = logging.getLogger(__name__)


class MRLOShotFileManager(MShotFileManager):
    def __init__(self):
        super().__init__(version_glob="{}*.{}", version_msg="Open alt version")

    @staticmethod
    def _check_unsaved_changes() -> bool:
        return True

    def _get_subpath(self) -> str:
        return "rlo"

    def _setup_scene(self) -> None:
        self._import_env()

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        if not path.exists():
            prompt_create = MessageDialogCustomButtons(
                self._main_window,
                f"The RLO file for shot {entity.code} does not exist. Continue "
                "to save a copy of the current file as the RLO file?",
                has_cancel_button=True,
                ok_name="Continue",
                cancel_name="Cancel",
            )
            if not bool(prompt_create.exec_()):
                return
        super()._setup_file(path, entity)

    def _resolve_current_rlo_stream(
        self,
        scene_path: Path,
    ) -> tuple[Shot, VersionStreamSpec] | None:
        shot = self._resolve_shot_for_scene(scene_path)
        if shot is None:
            return None

        stream = maya_rlo_stream(shot, owner=shot_owner_for(shot))
        if not path_matches_stream(scene_path, stream):
            return None
        return shot, stream

    def open_version_browser(self) -> None:
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                "No valid RLO shot file is open. Use Open RLO first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_rlo_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current scene to a valid RLO shot file. Use Open RLO first.",
                "Version History",
            ).exec_()
            return

        shot, rlo_stream = resolved
        records = list_version_records(rlo_stream)
        if not records:
            MessageDialog(
                self._main_window,
                "No version history was found for this shot.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            owner_label=shot.code,
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
                    self._main_window,
                    "The selected version has no backup file path.",
                    "Open Version Failed",
                ).exec_()
                return
            if not backup_path.exists() or not backup_path.is_file():
                MessageDialog(
                    self._main_window,
                    f"Backup file is missing on disk:\n{backup_path}",
                    "Open Version Failed",
                ).exec_()
                return
            if not MShotFileManager._check_unsaved_changes(self):
                return

            try:
                self._open_file(backup_path)
                self._post_open_file(shot)
            except Exception as exc:
                log.exception("Failed to open RLO backup version: %s", backup_path)
                MessageDialog(
                    self._main_window,
                    f"Failed to open selected version:\n{exc}",
                    "Open Version Failed",
                ).exec_()
            return

        if selected_action == VersionBrowserWidget.ACTION_PROMOTE:
            source_backup = selected_record.backup_path
            if source_backup is None or not source_backup.exists():
                MessageDialog(
                    self._main_window,
                    "Cannot create a new version from this entry because the backup file is missing.",
                    "Create Version Failed",
                ).exec_()
                return

            promote_dialog = PromoteVersionDialog(self._main_window, selected_record)
            if not promote_dialog.exec_():
                return

            try:
                promoted_record = promote_version(
                    selected_record,
                    rlo_stream,
                    title=promote_dialog.get_title(),
                    note=promote_dialog.get_note(),
                )
            except Exception as exc:
                log.exception("Failed to create a new RLO version.")
                MessageDialog(
                    self._main_window,
                    f"Failed to create new version:\n{exc}",
                    "Create Version Failed",
                ).exec_()
                return

            MessageDialog(
                self._main_window,
                (
                    f'Created new version {version_label(promoted_record.version)} '
                    f'"{promoted_record.title or "(untitled)"}" from the selected backup.\n'
                    "Open it from Version History to continue working from it."
                ),
                "Version Created",
            ).exec_()

    def save_version_for_current_scene(self) -> None:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return

        resolved = self._resolve_current_rlo_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current scene to a valid RLO shot file.",
                "Shot Not Resolved",
            ).exec_()
            return

        _shot, rlo_stream = resolved
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return

        try:
            version_record = save_version(
                scene_path,
                rlo_stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save manual RLO version.")
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            (
                f'Saved {version_label(version_record.version)} '
                f'"{version_record.title or "(untitled)"}".'
            ),
            "Version Saved",
        ).exec_()
