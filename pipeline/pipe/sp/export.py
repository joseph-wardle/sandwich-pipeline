"""High-level orchestration for Substance Painter texture export."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import substance_painter as sp
from Qt import QtWidgets

if TYPE_CHECKING:
    import typing

from shared.util import resolve_mapped_path
from substance_painter.exception import ProjectError

from pipe.asset.paths import paths_for_asset
from pipe.sp.export_config import (
    count_udim_sets,
    generate_export_config,
    resolve_export_targets,
)
from pipe.sp.export_material_info import write_material_info
from pipe.sp.export_results import (
    capture_export_events,
    existing_source_file_count,
    normalize_texture_export_map,
    planned_export_count,
    resolve_exported_files,
)
from pipe.sp.export_types import (
    ResolvedExportTarget,
    TargetExportOutcome,
    TexSetExportSettings,
)
from pipe.sp.progress import (
    PublishProgressCallback,
    PublishProgressUpdate,
    PublishStage,
)
from pipe.struct.db import Asset
from pipe.texconverter import TexConversionError, TexConverter

log = logging.getLogger(__name__)


class Exporter:
    """Export Painter textures, write publish metadata, and build TEX files."""

    _asset: Asset
    _out_path: Path
    _preview_path: Path
    _src_path: Path
    _tex_path: Path

    def __init__(self, asset: Asset) -> None:
        self._asset = asset
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

    def _set_error_message(self, message: str) -> None:
        self._last_error_message = message.strip()

    def _src_lock_path(self) -> Path:
        return self._src_path / ".lock"

    def _cleanup_export_lock(self, *, context: str) -> None:
        lock_path = self._src_lock_path()
        if not lock_path.exists():
            return
        try:
            lock_path.unlink()
            log.warning(f"Removed stale Substance export lock {lock_path} ({context})")
        except OSError:
            log.exception(
                f"Failed to remove Substance export lock {lock_path} ({context})"
            )

    def _preflight_exports(
        self,
        resolved_targets: list[ResolvedExportTarget],
        *,
        progress_callback: PublishProgressCallback | None = None,
    ) -> dict[str, dict[tuple[str, str], list[str]]]:
        """Validate export config and collect planned exports for all targets."""
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

        config = generate_export_config(self._src_path, resolved_targets)
        log.debug(config)
        try:
            all_planned = sp.export.list_project_textures(config)
        except (ProjectError, ValueError) as exc:
            raise ValueError(
                "Export configuration is invalid.\n"
                "Check enabled texture sets and channel settings, then try again.\n"
                f"Details: {exc}"
            ) from exc

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
        target: ResolvedExportTarget,
        *,
        planned_exports: dict[tuple[str, str], list[str]],
        target_index: int,
        target_count: int,
        progress_callback: PublishProgressCallback | None = None,
    ) -> TargetExportOutcome:
        """Export a single texture set and resolve the output file list."""
        config = generate_export_config(self._src_path, [target])
        target_planned_count = planned_export_count(planned_exports)

        if progress_callback is not None:
            progress_callback(
                PublishProgressUpdate(
                    stage=PublishStage.EXPORTING_SOURCE,
                    message=(
                        "Exporting source textures from Substance Painter "
                        f"for texture set {target_index}/{target_count}: "
                        f"{target.texture_set_name} "
                        f"({target_planned_count} file(s))."
                    ),
                    current=target_index - 1,
                    total=target_count,
                )
            )

        export_started_at_unix = time.time()
        event_snapshot, disconnect_export_events = capture_export_events()
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
                f'Texture export completed with warnings for "{target.texture_set_name}": '
                f"{export_result.message}"
            )
        elif export_result.status != sp.export.ExportStatus.Success:
            result_message = str(getattr(export_result, "message", "") or "").strip()
            raise RuntimeError(
                "Texture export failed for texture set "
                f'"{target.texture_set_name}" with status {export_result.status}.'
                + (f"\nSubstance message: {result_message}" if result_message else "")
            )

        try:
            exported_textures = resolve_exported_files(
                export_result,
                planned_exports,
                event_snapshot,
                started_at_unix=export_started_at_unix,
                src_path=self._src_path,
                logger=log,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                "Texture export produced no usable file list for texture set "
                f'"{target.texture_set_name}".\n{exc}'
            ) from exc

        returned_textures = normalize_texture_export_map(export_result.textures)
        returned_texture_count = planned_export_count(returned_textures)
        event_texture_count = planned_export_count(event_snapshot.ended_textures or {})
        event_planned_texture_count = planned_export_count(
            event_snapshot.about_to_start_textures or {}
        )
        used_event_fallback = not any(returned_textures.values()) and bool(
            event_snapshot.ended_textures
        )

        if target_planned_count != event_planned_texture_count:
            log.warning(
                "Substance planned export count mismatch for "
                f'"{target.texture_set_name}": '
                f"list_project_textures={target_planned_count}, "
                f"ExportTexturesAboutToStart={event_planned_texture_count}"
            )
        if returned_texture_count != event_texture_count:
            log.warning(
                f'Substance export count mismatch for "{target.texture_set_name}": '
                f"return={returned_texture_count}, "
                f"ExportTexturesEnded={event_texture_count}"
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

        return TargetExportOutcome(
            planned_exports=planned_exports,
            exported_textures=exported_textures,
            returned_texture_count=returned_texture_count,
            event_texture_count=event_texture_count,
            event_planned_texture_count=event_planned_texture_count,
            used_event_fallback=used_event_fallback,
        )

    def write_mat_info(
        self, export_settings_arr: typing.Iterable[TexSetExportSettings]
    ) -> None:
        write_material_info(self._out_path, export_settings_arr)

    def export(
        self,
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
        mat_var: str,
        geo_var: str,
        material_layer: str,
        progress_callback: PublishProgressCallback | None = None,
    ) -> bool:
        """Export all requested texture sets, then convert the outputs to TEX."""
        self._last_error_message = None
        export_action_id = self._new_texture_action_id()
        export_started_at = time.perf_counter()
        export_payload = self._texture_export_payload(
            geo_variant=geo_var,
            material_variant=mat_var,
            renderman_variant=material_layer,
            texture_set_count=len(exp_setting_arr),
            udim_set_count=count_udim_sets(exp_setting_arr),
        )

        def _duration_ms() -> int:
            return max(0, int((time.perf_counter() - export_started_at) * 1000))

        self._init_paths(mat_var, geo_var, material_layer)
        log.info(f"Exporting textures to {self._out_path}")

        self._cleanup_export_lock(context="before export")
        preexisting_src_count = existing_source_file_count(self._src_path)

        try:
            resolved_targets = resolve_export_targets(exp_setting_arr)
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
            udim_set_count=count_udim_sets(
                [target.settings for target in resolved_targets]
            ),
        )

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
                    f'Texture export failed while processing texture set "{target.texture_set_name}".'
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

            planned_texture_count += planned_export_count(outcome.planned_exports)
            returned_texture_count += outcome.returned_texture_count
            event_texture_count += outcome.event_texture_count
            event_planned_texture_count += outcome.event_planned_texture_count
            used_event_fallback = used_event_fallback or outcome.used_event_fallback
            all_exported_textures.update(outcome.exported_textures)
            QtWidgets.QApplication.processEvents()

        export_payload["planned_texture_count"] = planned_texture_count
        export_payload["exported_texture_count"] = planned_export_count(
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

        exported_count = planned_export_count(all_exported_textures)
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
