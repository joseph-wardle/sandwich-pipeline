import logging
from pathlib import Path

import maya.cmds as mc

from pipe.core.ui import (
    RESTORE_CANCEL,
    RESTORE_SAVE_FIRST,
    MessageDialog,
    MessageDialogCustomButtons,
    prompt_restore_conflict,
)
from pipe.core.ui.save_version_dialog import SaveVersionDialog
from pipe.core.ui.version_browser import VersionBrowserWidget
from pipe.core.shot import maya_rlo_stream, shot_owner_for
from pipe.core.shotgrid import SGEntity, Shot, is_previs_shot_code
from pipe.core.versioning import (
    VersionRecord,
    VersionStreamSpec,
    list_version_records,
    path_matches_stream,
    resolve_working_file_version,
    restore_version,
    restored_message,
    saved_message,
)
from pipe.core.versioning import (
    save_version as _save_version,
)

from .shotfile_manager import MShotFileManager
from .sets import sync_shot_sets

log = logging.getLogger(__name__)


class MRLOShotFileManager(MShotFileManager):
    def __init__(self):
        super().__init__(version_glob="{}*.{}", version_msg="Open alt version")

    def _check_unsaved_changes(self) -> bool:
        return True

    def _get_subpath(self) -> str:
        return "rlo"

    def _filter_entities(self, entities: list[SGEntity]) -> list[SGEntity]:
        # Previs sequence proxies aren't real shots; RLO never opens them.
        return [e for e in entities if not is_previs_shot_code(e.code)]

    def _setup_scene(self) -> None:
        sync_shot_sets(self.shot)

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

    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        result = self._resolve_current_rlo_stream(scene_path)
        if result is None:
            return None
        shot, stream = result
        return stream, shot.code or "", shot

    def _current_scene_path(self) -> Path | None:
        scene_raw = mc.file(query=True, sceneName=True)
        if not isinstance(scene_raw, str) or not scene_raw:
            return None
        return Path(scene_raw).expanduser().resolve()

    def _ensure_scene_saved(self) -> Path | None:
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                "Scene must be saved before creating a version.",
                "Save Required",
            ).exec_()
            return None

        if mc.file(query=True, modified=True):
            response = mc.confirmDialog(
                title="Save Changes",
                message="This scene has unsaved changes. Save before creating a version?",
                button=["Save", "Cancel"],
                defaultButton="Save",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if response != "Save":
                return None
            try:
                mc.file(save=True, force=True)
            except Exception:
                MessageDialog(
                    self._main_window,
                    "Failed to save the current scene. Resolve any file issues and try again.",
                    "Save Failed",
                ).exec_()
                log.exception("Failed to save Maya shot scene before creating version.")
                return None
            scene_path = self._current_scene_path()
            if scene_path is None:
                MessageDialog(
                    self._main_window,
                    "Could not resolve the current scene path after save.",
                    "Save Failed",
                ).exec_()
                return None

        return scene_path

    def _resolve_shot_for_scene(self, scene_path: Path) -> Shot | None:
        shot_code = self._shot_code_from_file_info() or self._shot_code_from_scene_path(
            str(scene_path)
        )
        if not shot_code:
            return None

        shot = self._conn.get_shot(code=shot_code)
        if shot.code:
            mc.fileInfo("code", shot.code)
        return shot

    def open_version_browser(self) -> None:
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                "No RLO shot file is open. Use Open RLO first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current scene to an RLO shot file. "
                "Use Open RLO first.",
                "Version History",
            ).exec_()
            return

        stream, owner_label, shot = resolved
        records = list_version_records(stream)
        if not records:
            MessageDialog(
                self._main_window,
                "No version history was found for this RLO.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            owner_label=owner_label,
            stream_label=stream.label,
        )
        if not browser.exec_():
            return

        selected_record = browser.get_selected_record()
        selected_action = browser.get_selected_action()
        if selected_record is None:
            return

        if selected_action == VersionBrowserWidget.ACTION_RESTORE:
            self._restore_version(selected_record, stream, shot)

    def _restore_version(
        self, record: VersionRecord, stream: VersionStreamSpec, entity: Shot
    ) -> None:
        if self._has_unversioned_work(stream):
            choice = prompt_restore_conflict(self._main_window)
            if choice == RESTORE_CANCEL:
                return
            if choice == RESTORE_SAVE_FIRST and not self._save_named_version(stream):
                return

        try:
            working_path = restore_version(record, stream)
        except Exception as exc:
            log.exception("Failed to restore RLO version.")
            MessageDialog(
                self._main_window,
                f"Failed to restore version:\n{exc}",
                "Restore Version Failed",
            ).exec_()
            return

        try:
            self._open_file(working_path)
            self._post_open_file(entity)
        except Exception as exc:
            log.exception("Restored RLO version but could not open it.")
            MessageDialog(
                self._main_window,
                f"Restored the version but could not open it:\n{exc}",
                "Restore Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            restored_message(record),
            "Version Restored",
        ).exec_()

    def _has_unversioned_work(self, stream: VersionStreamSpec) -> bool:
        if mc.file(query=True, modified=True):
            return True
        return resolve_working_file_version(stream) is None

    def _write_named_version(self, scene_path: Path, stream: VersionStreamSpec) -> bool:
        """Prompt for a version title and write a backup of *scene_path*."""
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return False

        try:
            record = _save_version(
                scene_path,
                stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save RLO version.")
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return False

        MessageDialog(
            self._main_window,
            saved_message(record),
            "Version Saved",
        ).exec_()
        return True

    def _save_named_version(self, stream: VersionStreamSpec) -> bool:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return False
        return self._write_named_version(scene_path, stream)

    def save_version_for_current_scene(self) -> None:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return

        resolved = self._resolve_current_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current scene to an RLO shot file.",
                "Shot Not Resolved",
            ).exec_()
            return

        stream, _, _ = resolved
        self._write_named_version(scene_path, stream)
