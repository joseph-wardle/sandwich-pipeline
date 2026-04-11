from __future__ import annotations

import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import substance_painter as sp

if TYPE_CHECKING:
    import typing

    RT = typing.TypeVar("RT")  # return type

from env_sg import DB_Config
from shared.util import resolve_mapped_path
from substance_painter.exception import ProjectError, ServiceNotFoundError

from pipe.asset.paths import paths_for_asset
from pipe.db import DB
from pipe.sp.progress import (
    PublishProgressCallback,
    PublishProgressUpdate,
    PublishStage,
)
from pipe.sp.util import texture_set_name
from pipe.struct.db import Asset
from pipe.struct.material import (
    DisplacementSource,
    MaterialInfo,
    NormalSource,
    NormalType,
    TexSetInfo,
)
from pipe.texconverter import TexConversionError, TexConverter

lib_path = resolve_mapped_path(Path(__file__).parents[1] / "lib")
log = logging.getLogger(__name__)


@dataclass
class TexSetExportSettings:
    tex_set: sp.textureset.TextureSet
    extra_channels: set[sp.textureset.Channel]
    resolution: int
    displacement_source: DisplacementSource
    normal_type: NormalType
    normal_source: NormalSource


@dataclass(frozen=True)
class _ResolvedExportTarget:
    settings: TexSetExportSettings
    stack: sp.textureset.Stack
    texture_set_name: str


@dataclass
class _ExportEventSnapshot:
    about_to_start_textures: dict[tuple[str, str], list[str]] | None = None
    ended_status: sp.export.ExportStatus | None = None
    ended_message: str | None = None
    ended_textures: dict[tuple[str, str], list[str]] | None = None


@dataclass(frozen=True)
class _TargetExportOutcome:
    target: _ResolvedExportTarget
    planned_exports: dict[tuple[str, str], list[str]]
    exported_textures: dict[tuple[str, str], list[str]]
    returned_texture_count: int
    event_texture_count: int
    event_planned_texture_count: int
    used_event_fallback: bool


def _channel_export_name(channel: sp.textureset.Channel) -> str:
    label_attr = getattr(channel, "label", None)
    label = label_attr() if callable(label_attr) else label_attr
    if isinstance(label, str) and label:
        return label.replace(" ", "")
    return channel.type().name


def _stack_root_path(stack: sp.textureset.Stack) -> str:
    ts_name = texture_set_name(stack.material())
    stack_name = stack.name()
    return f"{ts_name}/{stack_name}" if stack_name else ts_name


class Exporter:
    """Class to manage exporting and converting textures"""

    _asset: Asset
    _conn: DB
    _out_path: Path
    _preview_path: Path
    _src_path: Path
    _tex_path: Path

    def __init__(self, asset: Asset) -> None:
        self._asset = asset
        self._conn = DB.Get(DB_Config)
        self._last_error_message: str | None = None

    @property
    def last_error_message(self) -> str | None:
        return self._last_error_message

    def _init_paths(self, mat_var: str, geo_var: str, material_layer: str) -> None:
        paths = paths_for_asset(self._asset)
        material_layer_dir = paths.publish_textures_layer_dir(
            geo_var, mat_var, material_layer
        )

        self._out_path = resolve_mapped_path(material_layer_dir)
        self._src_path = resolve_mapped_path(
            paths.publish_textures_src_dir(geo_var, mat_var, material_layer)
        )
        self._preview_path = resolve_mapped_path(
            paths.publish_textures_preview_dir(geo_var, mat_var, material_layer)
        )
        self._tex_path = self._out_path

        self._out_path.mkdir(parents=True, exist_ok=True)
        self._src_path.mkdir(parents=True, exist_ok=True)
        self._preview_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_export_targets(
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
    ) -> list[_ResolvedExportTarget]:
        targets: list[_ResolvedExportTarget] = []
        for export_settings in exp_setting_arr:
            ts_name = texture_set_name(export_settings.tex_set)
            try:
                stack = export_settings.tex_set.get_stack()
            except ValueError as exc:
                raise ValueError(
                    (
                        f'Texture Set "{ts_name}" uses material layering.\n'
                        "This publish tool currently supports non-layered texture "
                        "sets only."
                    )
                ) from exc

            targets.append(
                _ResolvedExportTarget(
                    settings=export_settings,
                    stack=stack,
                    texture_set_name=ts_name,
                )
            )
        return targets

    @staticmethod
    def _count_udim_sets(
        export_settings_arr: typing.Iterable[TexSetExportSettings],
    ) -> int:
        count = 0
        for export_settings in export_settings_arr:
            try:
                if export_settings.tex_set.has_uv_tiles():
                    count += 1
            except (ProjectError, ServiceNotFoundError):
                continue
        return count

    def _texture_export_asset_name(self) -> str:
        asset_name = str(getattr(self._asset, "name", "") or "").strip()
        if asset_name:
            return asset_name
        asset_path = getattr(self._asset, "asset_path", None)
        if asset_path:
            return Path(str(asset_path)).name
        return "unknown_asset"

    def _texture_export_scope(self) -> dict[str, str] | None:
        try:
            from pipe.telemetry import extract_scope
        except ImportError:
            return None
        scope = extract_scope(self._asset)
        return scope or None

    @staticmethod
    def _new_texture_action_id() -> str | None:
        try:
            from pipe.telemetry import new_action_id
        except ImportError:
            return None
        return new_action_id()

    def _texture_export_payload(
        self,
        *,
        geo_variant: str,
        material_variant: str,
        renderman_variant: str,
        texture_set_count: int,
        udim_set_count: int,
    ) -> dict[str, object]:
        return {
            "asset": self._texture_export_asset_name(),
            "geo_variant": str(geo_variant or "main"),
            "material_variant": str(material_variant or "main"),
            "renderman_variant": str(renderman_variant or "main"),
            "texture_set_count": max(0, int(texture_set_count)),
            "udim_set_count": max(0, int(udim_set_count)),
        }

    def _emit_texture_export_event(
        self,
        *,
        status: str,
        action_id: str | None,
        payload: dict[str, object],
        duration_ms: int,
        error_message: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        try:
            from pipe.telemetry import STATUS_ERROR, STATUS_SUCCESS, emit, events
            from pipe.telemetry.registry import ERROR_TEXTURE_EXPORT_FAILED
        except ImportError:
            return

        status_value = STATUS_SUCCESS if status == "success" else STATUS_ERROR

        error_data = None
        if status == "error":
            error_data = {
                "code": ERROR_TEXTURE_EXPORT_FAILED,
                "message": error_message or "Texture export failed",
                "exception_type": exception_type or "RuntimeError",
            }

        emit(
            events.EVENT_TEXTURE_EXPORT_SUBSTANCE,
            status=status_value,
            action_id=action_id,
            payload=payload,
            metrics={"duration_ms": max(0, int(duration_ms))},
            scope=self._texture_export_scope(),
            error=error_data,
        )

    def _set_error_message(self, message: str) -> str:
        self._last_error_message = message.strip()
        return self._last_error_message

    def _src_lock_path(self) -> Path:
        return self._src_path / ".lock"

    def _cleanup_export_lock(self, *, context: str) -> None:
        lock_path = self._src_lock_path()
        if not lock_path.exists():
            return
        try:
            lock_path.unlink()
            log.warning(
                "Removed stale Substance export lock %s (%s)",
                lock_path,
                context,
            )
        except OSError:
            log.exception(
                "Failed to remove Substance export lock %s (%s)",
                lock_path,
                context,
            )

    @staticmethod
    def _planned_export_count(
        exports_by_stack: dict[tuple[str, str], list[str]],
    ) -> int:
        return sum(len(paths) for paths in exports_by_stack.values())

    @staticmethod
    def _normalize_texture_export_map(
        textures: object,
    ) -> dict[tuple[str, str], list[str]]:
        if not isinstance(textures, dict):
            return {}

        normalized: dict[tuple[str, str], list[str]] = {}
        for key, paths in textures.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or not isinstance(paths, list)
            ):
                continue
            normalized[(str(key[0]), str(key[1]))] = [str(path) for path in paths]
        return normalized

    def _normalize_export_path(self, export_path: str) -> Path:
        path = Path(export_path)
        if path.is_absolute():
            return path
        return self._src_path / path

    def _find_recent_written_exports(
        self,
        planned_exports: dict[tuple[str, str], list[str]],
        *,
        started_at_unix: float,
    ) -> dict[tuple[str, str], list[str]]:
        recovered: dict[tuple[str, str], list[str]] = {}
        for stack_key, export_paths in planned_exports.items():
            written_paths: list[str] = []
            for export_path in export_paths:
                resolved_path = self._normalize_export_path(export_path)
                try:
                    stat = resolved_path.stat()
                except FileNotFoundError:
                    continue
                if not resolved_path.is_file() or stat.st_size <= 0:
                    continue
                if stat.st_mtime < started_at_unix - 5:
                    continue
                written_paths.append(str(resolved_path))
            if written_paths:
                recovered[stack_key] = written_paths
        return recovered

    def _existing_source_file_count(self) -> int:
        with suppress(FileNotFoundError):
            return sum(
                1
                for path in self._src_path.iterdir()
                if path.is_file() and path.name != ".lock"
            )
        return 0

    def _capture_export_events(
        self,
    ) -> tuple[
        _ExportEventSnapshot,
        typing.Callable[[], None],
    ]:
        snapshot = _ExportEventSnapshot()

        def _on_about_to_start(event: sp.event.ExportTexturesAboutToStart) -> None:
            snapshot.about_to_start_textures = self._normalize_texture_export_map(
                getattr(event, "textures", None)
            )

        def _on_export_ended(event: sp.event.ExportTexturesEnded) -> None:
            snapshot.ended_status = getattr(event, "status", None)
            message = getattr(event, "message", "")
            snapshot.ended_message = str(message or "").strip() or None
            snapshot.ended_textures = self._normalize_texture_export_map(
                getattr(event, "textures", None)
            )

        sp.event.DISPATCHER.connect_strong(
            sp.event.ExportTexturesAboutToStart,
            _on_about_to_start,
        )
        sp.event.DISPATCHER.connect_strong(
            sp.event.ExportTexturesEnded,
            _on_export_ended,
        )

        def _disconnect() -> None:
            for event_type, callback in (
                (sp.event.ExportTexturesAboutToStart, _on_about_to_start),
                (sp.event.ExportTexturesEnded, _on_export_ended),
            ):
                with suppress(Exception):
                    sp.event.DISPATCHER.disconnect(event_type, callback)

        return snapshot, _disconnect

    def _resolve_exported_files(
        self,
        export_result: sp.export.TextureExportResult,
        planned_exports: dict[tuple[str, str], list[str]],
        event_snapshot: _ExportEventSnapshot,
        *,
        started_at_unix: float,
    ) -> dict[tuple[str, str], list[str]]:
        returned_textures = {
            stack_key: list(export_paths)
            for stack_key, export_paths in self._normalize_texture_export_map(
                export_result.textures
            ).items()
            if export_paths
        }
        if returned_textures:
            return returned_textures

        ended_textures = {
            stack_key: list(export_paths)
            for stack_key, export_paths in (event_snapshot.ended_textures or {}).items()
            if export_paths
        }
        if ended_textures:
            log.warning(
                "Substance export return was empty, but ExportTexturesEnded reported %d files.",
                self._planned_export_count(ended_textures),
            )
            return ended_textures

        recent_writes = self._find_recent_written_exports(
            planned_exports,
            started_at_unix=started_at_unix,
        )
        recovered_count = self._planned_export_count(recent_writes)
        planned_count = self._planned_export_count(planned_exports)

        ended_status = (
            getattr(event_snapshot.ended_status, "name", None)
            or str(event_snapshot.ended_status or "").strip()
        )
        result_message = (
            str(getattr(export_result, "message", "") or "").strip() or None
        )

        details = [
            "Substance Painter finished writing textures, but its export API did not "
            "report any exported files.",
            f"Planned files: {planned_count}",
            f"Recent files written to disk: {recovered_count}",
            f"Existing files already in export folder before/after export: {self._existing_source_file_count()}",
        ]
        about_to_start_count = self._planned_export_count(
            event_snapshot.about_to_start_textures or {}
        )
        if about_to_start_count:
            details.append(
                f"ExportTexturesAboutToStart planned: {about_to_start_count}"
            )
        if ended_status:
            details.append(f"ExportTexturesEnded status: {ended_status}")
        if event_snapshot.ended_message:
            details.append(
                f"ExportTexturesEnded message: {event_snapshot.ended_message}"
            )
        if result_message:
            details.append(f"export_project_textures() message: {result_message}")
        if self._src_lock_path().exists():
            details.append(
                f"Painter left export lock file behind: {self._src_lock_path()}"
            )
        detail = "\n".join(details)
        raise RuntimeError(detail)

    def _preflight_exports(
        self,
        resolved_targets: list[_ResolvedExportTarget],
        *,
        progress_callback: PublishProgressCallback | None = None,
    ) -> dict[str, dict[tuple[str, str], list[str]]]:
        """Validate export config and collect planned exports for all targets.

        Calls ``list_project_textures`` once with a combined config, then
        partitions the results by texture set name.  Raises ``ValueError``
        early if the config is invalid or any target would produce no files.
        """
        if progress_callback is not None:
            progress_callback(
                PublishProgressUpdate(
                    stage=PublishStage.PLANNING_EXPORT,
                    message=(
                        f"Validating export configuration for "
                        f"{len(resolved_targets)} texture set(s)."
                    ),
                )
            )

        config = Exporter._generate_config(self._src_path, resolved_targets)
        log.debug(config)
        try:
            all_planned = sp.export.list_project_textures(config)
        except (ProjectError, ValueError) as exc:
            raise ValueError(
                "Export configuration is invalid.\n"
                "Check enabled texture sets and channel settings, then try again.\n"
                f"Details: {exc}"
            ) from exc

        # Partition planned exports by texture set name (first element of the
        # (texture_set_name, stack_name) key that list_project_textures returns).
        planned_by_target: dict[str, dict[tuple[str, str], list[str]]] = {}
        for (ts_name, stack_name), paths in all_planned.items():
            target_planned = planned_by_target.setdefault(ts_name, {})
            target_planned[(ts_name, stack_name)] = paths

        for target in resolved_targets:
            target_planned = planned_by_target.get(target.texture_set_name, {})
            if not any(target_planned.values()):
                raise ValueError(
                    "No textures match the current export configuration for "
                    f'texture set "{target.texture_set_name}".'
                )

        return planned_by_target

    def _export_target(
        self,
        target: _ResolvedExportTarget,
        *,
        planned_exports: dict[tuple[str, str], list[str]],
        target_index: int,
        target_count: int,
        progress_callback: PublishProgressCallback | None = None,
    ) -> _TargetExportOutcome:
        config = Exporter._generate_config(self._src_path, [target])

        planned_export_count = self._planned_export_count(planned_exports)

        if progress_callback is not None:
            progress_callback(
                PublishProgressUpdate(
                    stage=PublishStage.EXPORTING_SOURCE,
                    message=(
                        "Exporting source textures from Substance Painter "
                        f"for texture set {target_index}/{target_count}: "
                        f"{target.texture_set_name} "
                        f"({planned_export_count} file(s))."
                    ),
                    current=target_index - 1,
                    total=target_count,
                )
            )

        export_started_at_unix = time.time()
        event_snapshot, disconnect_export_events = self._capture_export_events()
        try:
            export_result = sp.export.export_project_textures(config)
        except (ProjectError, ValueError) as exc:
            disconnect_export_events()
            self._cleanup_export_lock(
                context=f'after export exception for "{target.texture_set_name}"'
            )
            raise RuntimeError(
                "Substance Painter failed while exporting texture set "
                f'"{target.texture_set_name}".\n'
                f"Details: {exc}"
            ) from exc
        disconnect_export_events()
        self._cleanup_export_lock(
            context=f'after export for "{target.texture_set_name}"'
        )

        if export_result.status == sp.export.ExportStatus.Cancelled:
            raise RuntimeError(
                "Texture export was cancelled while exporting texture set "
                f'"{target.texture_set_name}".'
            )

        if export_result.status == sp.export.ExportStatus.Warning:
            log.warning(
                'Texture export completed with warnings for "%s": %s',
                target.texture_set_name,
                export_result.message,
            )
        elif export_result.status != sp.export.ExportStatus.Success:
            result_message = str(getattr(export_result, "message", "") or "").strip()
            raise RuntimeError(
                "Texture export failed for texture set "
                f'"{target.texture_set_name}" with status {export_result.status}.'
                + (f"\nSubstance message: {result_message}" if result_message else "")
            )

        try:
            exported_textures = self._resolve_exported_files(
                export_result,
                planned_exports,
                event_snapshot,
                started_at_unix=export_started_at_unix,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                "Texture export produced no usable file list for texture set "
                f'"{target.texture_set_name}".\n{exc}'
            ) from exc

        returned_texture_count = self._planned_export_count(
            self._normalize_texture_export_map(export_result.textures)
        )
        event_texture_count = self._planned_export_count(
            event_snapshot.ended_textures or {}
        )
        event_planned_texture_count = self._planned_export_count(
            event_snapshot.about_to_start_textures or {}
        )
        used_event_fallback = not any(export_result.textures.values()) and bool(
            event_snapshot.ended_textures
        )

        if planned_export_count != event_planned_texture_count:
            log.warning(
                'Substance planned export count mismatch for "%s": list_project_textures=%s, ExportTexturesAboutToStart=%s',
                target.texture_set_name,
                planned_export_count,
                event_planned_texture_count,
            )
        if returned_texture_count != event_texture_count:
            log.warning(
                'Substance export count mismatch for "%s": return=%s, ExportTexturesEnded=%s',
                target.texture_set_name,
                returned_texture_count,
                event_texture_count,
            )

        if progress_callback is not None:
            progress_callback(
                PublishProgressUpdate(
                    stage=PublishStage.EXPORTING_SOURCE,
                    message=(
                        "Finished source export for texture set "
                        f"{target_index}/{target_count}: {target.texture_set_name}."
                    ),
                    current=target_index,
                    total=target_count,
                )
            )

        return _TargetExportOutcome(
            target=target,
            planned_exports=planned_exports,
            exported_textures=exported_textures,
            returned_texture_count=returned_texture_count,
            event_texture_count=event_texture_count,
            event_planned_texture_count=event_planned_texture_count,
            used_event_fallback=used_event_fallback,
        )

    def export(
        self,
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
        mat_var: str,
        geo_var: str,
        material_layer: str,
        progress_callback: PublishProgressCallback | None = None,
    ) -> bool:
        """Export all the textures of the given Texture Sets"""
        self._last_error_message = None
        export_action_id = self._new_texture_action_id()
        export_started_at = time.perf_counter()
        export_payload = self._texture_export_payload(
            geo_variant=geo_var,
            material_variant=mat_var,
            renderman_variant=material_layer,
            texture_set_count=len(exp_setting_arr),
            udim_set_count=self._count_udim_sets(exp_setting_arr),
        )

        def _duration_ms() -> int:
            return max(0, int((time.perf_counter() - export_started_at) * 1000))

        self._init_paths(mat_var, geo_var, material_layer)
        log.info("Exporting textures to %s", self._out_path)

        self._cleanup_export_lock(context="before export")
        preexisting_src_count = self._existing_source_file_count()

        try:
            resolved_targets = self._resolve_export_targets(exp_setting_arr)
        except ValueError as exc:
            self._set_error_message(str(exc))
            self._emit_texture_export_event(
                status="error",
                action_id=export_action_id,
                payload=export_payload,
                duration_ms=_duration_ms(),
                error_message=self._last_error_message,
                exception_type=type(exc).__name__,
            )
            return False

        export_payload = self._texture_export_payload(
            geo_variant=geo_var,
            material_variant=mat_var,
            renderman_variant=material_layer,
            texture_set_count=len(resolved_targets),
            udim_set_count=self._count_udim_sets(
                [target.settings for target in resolved_targets]
            ),
        )

        # Pre-flight: validate config and collect planned exports for all
        # targets in a single list_project_textures call.  This catches
        # invalid configs and empty texture sets before any export begins.
        try:
            planned_by_target = self._preflight_exports(
                resolved_targets, progress_callback=progress_callback
            )
        except ValueError as exc:
            self._set_error_message(str(exc))
            self._emit_texture_export_event(
                status="error",
                action_id=export_action_id,
                payload=export_payload,
                duration_ms=_duration_ms(),
                error_message=self._last_error_message,
                exception_type=type(exc).__name__,
            )
            return False

        all_exported_textures: dict[tuple[str, str], list[str]] = {}
        planned_texture_count = 0
        returned_texture_count = 0
        event_texture_count = 0
        event_planned_texture_count = 0
        used_event_fallback = False

        for target_index, target in enumerate(resolved_targets, start=1):
            self._cleanup_export_lock(
                context=f'before export for "{target.texture_set_name}"'
            )
            try:
                outcome = self._export_target(
                    target,
                    planned_exports=planned_by_target.get(target.texture_set_name, {}),
                    target_index=target_index,
                    target_count=len(resolved_targets),
                    progress_callback=progress_callback,
                )
            except RuntimeError as exc:
                log.error(
                    'Texture export failed while processing texture set "%s".',
                    target.texture_set_name,
                )
                self._set_error_message(str(exc))
                export_payload["planned_texture_count"] = planned_texture_count
                self._emit_texture_export_event(
                    status="error",
                    action_id=export_action_id,
                    payload=export_payload,
                    duration_ms=_duration_ms(),
                    error_message=self._last_error_message,
                    exception_type=type(exc).__name__,
                )
                return False

            planned_texture_count += self._planned_export_count(outcome.planned_exports)
            returned_texture_count += outcome.returned_texture_count
            event_texture_count += outcome.event_texture_count
            event_planned_texture_count += outcome.event_planned_texture_count
            used_event_fallback = used_event_fallback or outcome.used_event_fallback
            all_exported_textures.update(outcome.exported_textures)

        export_payload["planned_texture_count"] = planned_texture_count

        export_payload["exported_texture_count"] = self._planned_export_count(
            all_exported_textures
        )
        export_payload["preexisting_source_file_count"] = preexisting_src_count
        export_payload["returned_texture_count"] = returned_texture_count
        export_payload["event_texture_count"] = event_texture_count
        export_payload["event_planned_texture_count"] = event_planned_texture_count
        export_payload["used_event_fallback"] = used_event_fallback

        try:
            if progress_callback is not None:
                progress_callback(
                    PublishProgressUpdate(
                        stage=PublishStage.WRITING_METADATA,
                        message="Writing material metadata for the published textures.",
                    )
                )
            self.write_mat_info([target.settings for target in resolved_targets])
        except (OSError, ValueError) as exc:
            log.exception("Failed to write material info metadata.")
            self._set_error_message(
                "Textures exported, but failed to write material metadata.\n"
                f"Details: {exc}"
            )
            self._emit_texture_export_event(
                status="error",
                action_id=export_action_id,
                payload=export_payload,
                duration_ms=_duration_ms(),
                error_message=self._last_error_message,
                exception_type=type(exc).__name__,
            )
            return False

        self._emit_texture_export_event(
            status="success",
            action_id=export_action_id,
            payload=export_payload,
            duration_ms=_duration_ms(),
        )

        exported_count = self._planned_export_count(all_exported_textures)
        sp.logging.info(
            f"Exported {exported_count} texture(s) for "
            f"{self._texture_export_asset_name()} to {self._out_path}"
        )

        tex_converter = TexConverter(
            self._tex_path,
            self._preview_path,
            list(all_exported_textures.values()),
            action_id=export_action_id,
            asset_name=self._texture_export_asset_name(),
            geo_variant=geo_var,
            material_variant=mat_var,
            renderman_variant=material_layer,
            progress_callback=progress_callback,
        )

        try:
            tex_converter.convert_all()
        except TexConversionError:
            log.exception("Texture conversion failed.")
            sp.logging.warning(
                "TEX conversion failed; source textures exported but .tex files were not generated."
            )
            self._set_error_message(
                "Source textures exported, but TEX conversion failed.\n"
                "Stop rendering this asset in Houdini and press "
                '"Reset RenderMan RIS/XPU", then try again.'
            )
            return False

        return True

    def write_mat_info(
        self, export_settings_arr: typing.Iterable[TexSetExportSettings]
    ) -> bool:
        """Write out JSON file with information about the texturesets"""
        mat_info_path = self._out_path / "mat.json"
        old_mat_info: MaterialInfo
        if mat_info_path.exists():
            with open(mat_info_path, "r") as f:
                old_mat_info = MaterialInfo.from_json(f.read())
        else:
            old_mat_info = MaterialInfo()

        all_tex_sets = [texture_set_name(ts) for ts in sp.textureset.all_texture_sets()]
        for tex_set in list(old_mat_info.tex_sets.keys()):
            if tex_set not in all_tex_sets:
                del old_mat_info.tex_sets[tex_set]

        new_mat_info = MaterialInfo(
            {
                **old_mat_info.tex_sets,
                **{
                    texture_set_name(export_settings.tex_set): TexSetInfo(
                        displacement_source=export_settings.displacement_source,
                        has_udims=export_settings.tex_set.has_uv_tiles(),
                        normal_source=export_settings.normal_source,
                        normal_type=export_settings.normal_type,
                    )
                    for export_settings in export_settings_arr
                },
            }
        )
        with open(str(self._out_path / "mat.json"), "w", encoding="utf-8") as f:
            f.write(new_mat_info.to_json())
        return True

    @staticmethod
    def _generate_config(
        src_path: Path, export_targets: typing.Iterable[_ResolvedExportTarget]
    ) -> dict:
        targets = list(export_targets)
        return {
            "exportPath": str(src_path),
            "exportShaderParams": True,
            "exportPresets": [
                {
                    "name": target.texture_set_name,
                    "maps": [
                        # Default RenderMan maps
                        *Exporter._shader_maps(target.settings),
                        # Extra AOVs
                        *[
                            {
                                "fileName": f"$textureSet_{_channel_export_name(ch)}(_$colorSpace)(.$udim)",
                                "channels": [
                                    {
                                        "destChannel": color,
                                        "srcChannel": color,
                                        "srcMapType": "documentMap",
                                        "srcMapName": ch.type().name.lower(),
                                    }
                                    for color in colors
                                ],
                                "parameters": {
                                    "bitDepth": bit_depth.lower(),
                                    "fileFormat": "png",
                                    "sizeLog2": target.settings.resolution,
                                },
                            }
                            for ch in target.settings.extra_channels
                            for colors, bit_depth in re.findall(
                                r"^s?(L|RGB)(\d{1,2}F?)$",
                                target.stack.get_channel(ch.type()).format().name,
                            )
                        ],
                        # Preview Surface
                        *Exporter._preview_surface_maps(),
                    ],
                }
                for target in targets
            ],
            "exportList": [
                {
                    "rootPath": _stack_root_path(target.stack),
                    "exportPreset": target.texture_set_name,
                }
                for target in targets
            ],
            "exportParameters": [
                {
                    "parameters": {
                        "dithering": False,
                        "paddingAlgorithm": "color",
                        "dilationDistance": 24,
                    }
                }
            ],
        }

    @staticmethod
    def _shader_maps(export_settings: TexSetExportSettings) -> list:
        maps = [
            {
                "fileName": "$textureSet_BaseColor(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        "srcMapType": "documentMap",
                        "srcMapName": "baseColor",
                    }
                    for ch in "RGB"
                ],
                "parameters": {
                    "bitDepth": "16",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            },
            {
                "fileName": "$textureSet_Metallic(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": "L",
                        "srcChannel": "L",
                        "srcMapType": "documentMap",
                        "srcMapName": "metallic",
                    },
                ],
                "parameters": {
                    "bitDepth": "8",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            },
            {
                "fileName": "$textureSet_IOR(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": "L",
                        "srcChannel": "L",
                        "srcMapType": "documentMap",
                        "srcMapName": "specular",
                    },
                ],
                "parameters": {
                    "bitDepth": "8",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            },
            {
                "fileName": "$textureSet_SpecularRoughness(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": "L",
                        "srcChannel": "L",
                        "srcMapType": "documentMap",
                        "srcMapName": "roughness",
                    },
                ],
                "parameters": {
                    "bitDepth": "8",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            },
            {
                "fileName": "$textureSet_Emissive(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        "srcMapType": "documentMap",
                        "srcMapName": "emissive",
                    }
                    for ch in "RGB"
                ],
                "parameters": {
                    "bitDepth": "16",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            },
            {
                "fileName": "$textureSet_Presence(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": "L",
                        "srcChannel": "L",
                        "srcMapType": "documentMap",
                        "srcMapName": "opacity",
                    },
                ],
                "parameters": {
                    "bitDepth": "8",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            },
            {
                "fileName": f"$textureSet_Normal(_$colorSpace)(.$udim){'.pre-b2r' if export_settings.normal_type == NormalType.BUMP_ROUGHNESS else ''}",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        **(
                            {
                                "srcMapType": "virtualMap",
                                "srcMapName": "Normal_OpenGL",
                            }
                            if export_settings.normal_source
                            is NormalSource.NORMAL_HEIGHT
                            else {
                                "srcMapType": "documentMap",
                                "srcMapName": "normal",
                            }
                        ),
                    }
                    for ch in "RGB"
                ],
                "parameters": {
                    **(
                        {
                            "bitDepth": "16f",
                            "fileFormat": "exr",
                        }
                        if export_settings.normal_type is NormalType.BUMP_ROUGHNESS
                        else {
                            "bitDepth": "16",
                            "fileFormat": "png",
                        }
                    ),
                    "sizeLog2": export_settings.resolution,
                },
            },
        ]

        if export_settings.displacement_source is not DisplacementSource.NONE:
            maps += [
                {
                    "fileName": "$textureSet_Displacement(_$colorSpace)(.$udim)",
                    "channels": [
                        {
                            "destChannel": "L",
                            "srcChannel": "L",
                            "srcMapType": "documentMap",
                            "srcMapName": (
                                "height"
                                if export_settings.displacement_source
                                == DisplacementSource.HEIGHT
                                else "displacement"
                            ),
                        },
                    ],
                    "parameters": {
                        "bitDepth": "16",
                        "fileFormat": "png",
                        "sizeLog2": export_settings.resolution,
                    },
                }
            ]

        return maps

    @staticmethod
    def _preview_surface_maps() -> list:
        return [
            {
                "fileName": "$textureSet_DiffuseColor(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        "srcMapType": "documentMap",
                        "srcMapName": "baseColor",
                    }
                    for ch in "RGB"
                ],
                "parameters": {
                    "bitDepth": "8",
                    "dithering": True,
                    "fileFormat": "jpeg",
                },
            },
            {
                "fileName": "$textureSet_ORM(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": "R",
                        "srcChannel": "R",
                        "srcMapType": "documentMap",
                        "srcMapName": "ambientOcclusion",
                    },
                    {
                        "destChannel": "G",
                        "srcChannel": "G",
                        "srcMapType": "documentMap",
                        "srcMapName": "roughness",
                    },
                    {
                        "destChannel": "B",
                        "srcChannel": "B",
                        "srcMapType": "documentMap",
                        "srcMapName": "metallic",
                    },
                ],
                "parameters": {
                    "bitDepth": "8",
                    "fileFormat": "jpeg",
                },
            },
            {
                "fileName": "$textureSet_Emissive(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        "srcMapType": "documentMap",
                        "srcMapName": "emissive",
                    }
                    for ch in "RGB"
                ],
                "parameters": {
                    "bitDepth": "8",
                    "dithering": True,
                    "fileFormat": "jpeg",
                },
            },
            {
                "fileName": "$textureSet_NormalDX(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        "srcMapType": "virtualMap",
                        "srcMapName": "Normal_DirectX",
                    }
                    for ch in "RGB"
                ],
                "parameters": {
                    "bitDepth": "8",
                    "fileFormat": "jpeg",
                },
            },
        ]
