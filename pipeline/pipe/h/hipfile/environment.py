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
from pipe.struct.db import Environment, SGEntity, normalize_display_name
from pipe.versioning import VersionStreamSpec

from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HEnvFileManager(HFileManager):
    def __init__(self) -> None:
        super().__init__(Environment)

    def _entity_label(self) -> str:
        return "set"

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

    def _resolve_current_stream(
        self, hip_path: Path
    ) -> tuple[VersionStreamSpec, str, SGEntity] | None:
        resolved = self._resolve_current_set_stream(hip_path)
        if resolved is None:
            return None
        environment, stream = resolved
        return (
            stream,
            environment.display_name or environment.name or "Set",
            environment,
        )

    def save_version(self) -> None:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return

        resolved = self._resolve_current_stream(hip_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current HIP to a valid set file.",
                "Set Not Resolved",
            ).exec_()
            return

        stream, _, _ = resolved
        self._do_save_version(hip_path, stream)
