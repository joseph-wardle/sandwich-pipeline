from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou
from core.util.util import get_production_path

from core.environment.version_adapter import (
    environment_owner_for,
    houdini_set_stream,
)
from core.versioning import path_matches_stream
from core.glui.dialogs import MessageDialog
from core.shotgrid import (
    Environment,
    SGEntity,
    ShotGridError,
    ShotGridNotFound,
    normalize_display_name,
)
from core.versioning import VersionStreamSpec

from ..publish import nodelayouts
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
        hou.setContextOption("ENVIRON", environment.name)

        try:
            nodelayouts.ensure_skd_layout()
        except Exception:
            log.exception("Failed to ensure SKD layout for %s", environment.name)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        super()._setup_file(path, entity)

    def _resolve_environment_from_context(
        self, context_value: str
    ) -> Environment | None:
        normalized_context = str(context_value).strip()
        if not normalized_context:
            return None

        try:
            return self._conn.get_environment(code=normalized_context)
        except ShotGridNotFound:
            # Expected when the context option is a normalized name (or similar
            # near-miss); fall through to the slower normalized-name search.
            pass
        except ShotGridError:
            log.warning(
                "ShotGrid lookup for environment code %r failed; "
                "trying normalized-name fallback.",
                normalized_context,
                exc_info=True,
            )

        normalized_name = normalize_display_name(normalized_context)
        if not normalized_name:
            return None

        return self._find_environment_by_normalized_name(normalized_name)

    @staticmethod
    def _environment_root_relative_for_hip(hip_path: Path) -> str | None:
        try:
            relative_path = hip_path.resolve().relative_to(get_production_path())
        except (OSError, ValueError):
            # OSError: the HIP doesn't resolve (network mount glitch, missing file).
            # ValueError: the HIP lives outside the production root.
            # Both mean we can't derive an environment from the path.
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
        # ``hou.contextOption`` returns ``None`` (which ``str()`` happily turns
        # into ``"None"``) when the option is unset; the broad catch is here
        # because Houdini may also raise ``hou.OperationFailed`` while a scene
        # is mid-load and we still need a usable fallback.
        try:
            context_environment = str(hou.contextOption("ENVIRON")).strip()
        except Exception:
            log.debug(
                "hou.contextOption('ENVIRON') unavailable; ignoring.", exc_info=True
            )
            context_environment = ""

        if context_environment:
            from_context = self._resolve_environment_from_context(context_environment)
            if from_context is not None:
                return from_context

        relative_root = self._environment_root_relative_for_hip(hip_path)
        if relative_root:
            env_name_from_path = normalize_display_name(Path(relative_root).name)
            if env_name_from_path:
                from_path = self._find_environment_by_normalized_name(
                    env_name_from_path
                )
                if from_path is not None:
                    return from_path

        normalized_stem = normalize_display_name(hip_path.stem.rsplit(".v", 1)[0])
        if not normalized_stem:
            return None
        return self._find_environment_by_normalized_name(normalized_stem)

    def _find_environment_by_normalized_name(
        self, normalized_name: str
    ) -> Environment | None:
        """Walk every Environment and return the first whose normalized name matches."""
        try:
            envs = self._conn.find_environments()
        except ShotGridError:
            log.warning(
                "Could not list environments while resolving %r; treating as no match.",
                normalized_name,
                exc_info=True,
            )
            return None
        for env in envs:
            if normalize_display_name(env.code) == normalized_name:
                return env
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
                environment.environment_path,
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
