from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou
from shared.util import get_production_path

from pipe.environment.version_adapter import (
    environment_owner_for,
    houdini_set_stream,
    path_matches_stream,
)
from pipe.glui.dialogs import MessageDialog
from pipe.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from pipe.glui.version_browser import VersionBrowserWidget
from pipe.struct.db import Environment, SGEntity, normalize_display_name
from pipe.versioning import (
    VersionStreamSpec,
    list_version_records,
    promote_version as promote_version_record,
    save_version as save_version_record,
    version_label,
)

from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HEnvFileManager(HFileManager):
    def __init__(self) -> None:
        super().__init__(Environment)

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        env = cast(Environment, entity)
        return env.name, "hipnc"

    def _post_open_file(self, entity: SGEntity) -> None:
        environment = cast(Environment, entity)
        context_value = (
            (environment.path or "").strip()
            or (environment.code or "").strip()
            or environment.name
        )
        if context_value:
            hou.setContextOption("ENVIRON", context_value)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        super(HEnvFileManager, HEnvFileManager)._setup_file(self, path, entity)

    @staticmethod
    def _current_hip_path() -> Path | None:
        hip_raw = (hou.hipFile.path() or "").strip()
        if not hip_raw:
            return None

        hip_path = Path(hou.expandString(hip_raw)).expanduser()
        if not hip_path.is_absolute():
            hip_path = (Path(hou.hscriptStringExpression("$HIP")) / hip_path).resolve()
        else:
            hip_path = hip_path.resolve()
        return hip_path

    def _resolve_environment_from_context(
        self, context_value: str
    ) -> Environment | None:
        normalized_context = str(context_value).strip()
        if not normalized_context:
            return None

        for resolver in (
            lambda: self._conn.get_env_by_code(normalized_context),
            lambda: self._conn.get_env_by_attr("path", normalized_context),
        ):
            try:
                resolved = resolver()
            except Exception:
                continue
            if isinstance(resolved, Environment):
                return resolved

        normalized_name = normalize_display_name(normalized_context)
        if not normalized_name:
            return None

        try:
            env_codes = self._conn.get_env_code_list(sorted=False)
        except Exception:
            return None

        for env_code in env_codes:
            if normalize_display_name(env_code) != normalized_name:
                continue
            try:
                resolved = self._conn.get_env_by_code(env_code)
            except Exception:
                continue
            if isinstance(resolved, Environment):
                return resolved
        return None

    @staticmethod
    def _environment_root_relative_for_hip(hip_path: Path) -> str | None:
        try:
            relative_path = hip_path.resolve().relative_to(get_production_path())
        except Exception:
            return None

        if ".backup" in relative_path.parts:
            backup_index = relative_path.parts.index(".backup")
            if backup_index <= 0:
                return None
            return Path(*relative_path.parts[:backup_index]).as_posix()

        parent_path = relative_path.parent
        if str(parent_path) == ".":
            return None
        return parent_path.as_posix()

    def _resolve_environment_for_hip(self, hip_path: Path) -> Environment | None:
        try:
            context_environment = str(hou.contextOption("ENVIRON")).strip()
        except Exception:
            context_environment = ""

        if context_environment:
            from_context = self._resolve_environment_from_context(context_environment)
            if from_context is not None:
                return from_context

        relative_root = self._environment_root_relative_for_hip(hip_path)
        if relative_root:
            try:
                from_path = self._conn.get_env_by_attr("path", relative_root)
            except Exception:
                from_path = None
            if isinstance(from_path, Environment):
                return from_path

        normalized_stem = normalize_display_name(hip_path.stem.rsplit(".v", 1)[0])
        if not normalized_stem:
            return None

        try:
            env_codes = self._conn.get_env_code_list(sorted=False)
        except Exception:
            return None

        for env_code in env_codes:
            if normalize_display_name(env_code) != normalized_stem:
                continue
            try:
                resolved = self._conn.get_env_by_code(env_code)
            except Exception:
                continue
            if isinstance(resolved, Environment):
                return resolved
        return None

    def _resolve_current_set_stream(
        self,
        hip_path: Path,
    ) -> tuple[Environment, VersionStreamSpec] | None:
        environment = self._resolve_environment_for_hip(hip_path)
        if environment is None:
            return None

        try:
            stream = houdini_set_stream(
                environment,
                owner=environment_owner_for(environment),
            )
        except ValueError:
            log.warning(
                "Could not resolve set stream for environment %s with path %s",
                environment.code,
                environment.path,
            )
            return None

        if not path_matches_stream(hip_path, stream):
            return None
        return environment, stream

    def _ensure_hip_saved(self) -> Path | None:
        hip_path = self._current_hip_path()
        if hip_path is None:
            MessageDialog(
                self._main_window,
                "Current HIP has no file path. Save the project before creating a version.",
                "Save Required",
            ).exec_()
            return None

        if hou.hipFile.hasUnsavedChanges():
            response = hou.ui.displayMessage(
                "The current HIP has unsaved changes. Save before creating a version?",
                buttons=("Save", "Cancel"),
                severity=hou.severityType.ImportantMessage,
                default_choice=0,
                close_choice=1,
            )
            if response != 0:
                return None
            try:
                hou.hipFile.save()
            except Exception:
                log.exception("Failed to save HIP before creating set version.")
                MessageDialog(
                    self._main_window,
                    "Failed to save the current HIP. Resolve file issues and try again.",
                    "Save Failed",
                ).exec_()
                return None
            hip_path = self._current_hip_path()
            if hip_path is None:
                MessageDialog(
                    self._main_window,
                    "Could not resolve HIP path after save.",
                    "Save Failed",
                ).exec_()
                return None

        if not hip_path.exists() or not hip_path.is_file():
            MessageDialog(
                self._main_window,
                f"HIP file does not exist on disk:\n{hip_path}",
                "Invalid HIP Path",
            ).exec_()
            return None
        return hip_path

    def open_version_browser(self) -> None:
        hip_path = self._current_hip_path()
        if hip_path is None:
            MessageDialog(
                self._main_window,
                "No valid set HIP is open. Use Open Set first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_set_stream(hip_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current HIP to a valid set file. Use Open Set first.",
                "Version History",
            ).exec_()
            return

        environment, set_stream = resolved
        records = list_version_records(set_stream)
        if not records:
            MessageDialog(
                self._main_window,
                "No version history was found for this set.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            owner_label=environment.display_name or environment.name or "Set",
            stream_label=set_stream.label,
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
            if not self._check_unsaved_changes():
                return

            load_warning: str | None = None
            try:
                load_warning = self._load_hip_file(backup_path)
            except Exception as exc:
                log.exception("Failed to load Houdini set version: %s", backup_path)
                MessageDialog(
                    self._main_window,
                    (
                        "Failed to open selected version:\n"
                        f"{self._describe_exception(exc, fallback='Could not load the HIP file')}"
                    ),
                    "Open Version Failed",
                ).exec_()
                return

            try:
                self._post_open_file(environment)
            except Exception as exc:
                log.exception(
                    "Loaded Houdini set version but post-open setup failed: %s",
                    backup_path,
                )
                MessageDialog(
                    self._main_window,
                    (
                        "The selected version loaded, but set setup could not finish:\n"
                        f"{self._describe_exception(exc, fallback='Set post-open setup failed')}"
                    ),
                    "Open Version Failed",
                ).exec_()
                return

            if load_warning:
                self._show_hip_load_warning(
                    path=backup_path,
                    warning=load_warning,
                    title="Version Opened With Warnings",
                )
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
                promoted_record = promote_version_record(
                    selected_record,
                    set_stream,
                    title=promote_dialog.get_title(),
                    note=promote_dialog.get_note(),
                )
            except Exception as exc:
                log.exception("Failed to create a new Houdini set version.")
                MessageDialog(
                    self._main_window,
                    f"Failed to create new version:\n{exc}",
                    "Create Version Failed",
                ).exec_()
                return

            MessageDialog(
                self._main_window,
                (
                    f"Created new version {version_label(promoted_record.version)} "
                    f'"{promoted_record.title or "(untitled)"}" from the selected backup.\n'
                    "Open it from Version History to continue working from it."
                ),
                "Version Created",
            ).exec_()

    def save_version(self) -> None:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return

        resolved = self._resolve_current_set_stream(hip_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current HIP to a valid set file.",
                "Set Not Resolved",
            ).exec_()
            return

        _environment, set_stream = resolved
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return

        try:
            version_record = save_version_record(
                hip_path,
                set_stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save Houdini set version.")
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            (
                f"Saved {version_label(version_record.version)} "
                f'"{version_record.title or "(untitled)"}".'
            ),
            "Version Saved",
        ).exec_()
