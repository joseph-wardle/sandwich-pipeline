"""High-level orchestration for Substance Painter texture export."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import substance_painter as sp
from Qt import QtWidgets

if TYPE_CHECKING:
    import typing

from pipe.core.util.paths import resolve_mapped_path
from substance_painter.exception import ProjectError

from pipe.core.asset import paths_for_asset
from pipe.core.shotgrid import Asset
from pipe.dcc.substance_painter.publish.config import (
    count_udim_sets,
    generate_export_config,
    resolve_export_targets,
)
from pipe.dcc.substance_painter.publish.material_info import write_material_info
from pipe.dcc.substance_painter.publish.types import (
    ResolvedExportTarget,
    TexSetExportSettings,
)
from pipe.dcc.substance_painter.util.progress import (
    PublishProgressCallback,
    PublishProgressUpdate,
    PublishStage,
)
from pipe.core import telemetry
from pipe.core.texture import TexConversionError, TexConverter

log = logging.getLogger(__name__)

# Painter writes the 1k UsdPreviewSurface maps as jpeg; every render map is png
# or exr, so the extension alone separates the two.
_PREVIEW_SUFFIX = ".jpeg"

_NOT_OCIO_MESSAGE = (
    "This project uses Painter's Legacy or Adobe ACE color management, which "
    "the pipeline can't publish.\n\n"
    "Open Edit → Project Configuration, expand Color management, change it to "
    "OpenColorIO and press OK. Then publish again."
)


class TextureExportError(Exception):
    """An export step failed; the message is shown to the artist as is."""

    error_code = "TEXTURE_EXPORT_FAILED"


def _preview_shares_render_name(planned: list[str]) -> bool:
    """Whether a preview jpeg is planned under the same name as a render map."""
    paths = [Path(path) for path in planned]
    previews = {path.stem for path in paths if path.suffix.lower() == _PREVIEW_SUFFIX}
    renders = {path.stem for path in paths if path.suffix.lower() != _PREVIEW_SUFFIX}
    return not previews.isdisjoint(renders)


def _file_count(files_by_stack: dict[tuple[str, str], list[str]]) -> int:
    return sum(len(paths) for paths in files_by_stack.values())


class Exporter:
    """Export Painter textures, write publish metadata, and build TEX files."""

    _asset: Asset
    _out_path: Path
    _preview_path: Path
    _src_path: Path

    def __init__(self, asset: Asset) -> None:
        self._asset = asset

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

        if any(_preview_shares_render_name(paths) for paths in all_planned.values()):
            raise ValueError(_NOT_OCIO_MESSAGE)

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
    ) -> dict[tuple[str, str], list[str]]:
        """Export a single texture set and return the files Painter wrote."""
        config = generate_export_config(self._src_path, [target])
        target_planned_count = _file_count(planned_exports)

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

        try:
            export_result = sp.export.export_project_textures(config)
        except (ProjectError, ValueError) as exc:
            self._cleanup_export_lock(
                context=f'after export exception for "{target.texture_set_name}"'
            )
            raise RuntimeError(
                "Substance Painter failed while exporting texture set "
                f'"{target.texture_set_name}".\n'
                f"Details: {exc}"
            ) from exc

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

        exported_textures = {
            stack_key: paths
            for stack_key, paths in export_result.textures.items()
            if paths
        }
        if not exported_textures:
            raise RuntimeError(
                "Substance Painter reported no exported files for texture set "
                f'"{target.texture_set_name}". Try publishing again.'
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

        return exported_textures

    def export(
        self,
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
        mat_var: str,
        geo_var: str,
        material_layer: str,
        progress_callback: PublishProgressCallback | None = None,
    ) -> None:
        """Export the texture sets, convert them to TEX, then write mat.json."""
        all_exported_textures = self._export_substance_textures(
            exp_setting_arr,
            mat_var=mat_var,
            geo_var=geo_var,
            material_layer=material_layer,
            progress_callback=progress_callback,
        )

        exported_count = _file_count(all_exported_textures)
        sp.logging.info(
            f"Exported {exported_count} texture(s) for "
            f"{self._texture_export_asset_name()} to {self._out_path}"
        )

        try:
            render_sources = self._move_previews(all_exported_textures)
        except OSError as exc:
            log.exception("Failed to move preview textures.")
            raise TextureExportError(
                f"Textures exported, but moving the preview jpegs into "
                f"{self._preview_path} failed.\nDetails: {exc}"
            ) from exc

        tex_converter = TexConverter(
            self._out_path,
            render_sources,
            asset_name=self._texture_export_asset_name(),
            geo_variant=geo_var,
            material_variant=mat_var,
            renderman_variant=material_layer,
            progress_callback=progress_callback,
        )

        try:
            tex_converter.convert_all()
        except TexConversionError as exc:
            log.exception("Texture conversion failed.")
            sp.logging.warning(
                "TEX conversion failed; source textures exported but .tex files were not generated."
            )
            raise TextureExportError(
                "Source textures exported, but TEX conversion failed.\n"
                f"Details: {exc}\n"
                "If this asset is rendering in Houdini, stop the render and press "
                '"Reset RenderMan RIS/XPU", then publish again.'
            ) from exc

        # Written last, so mat.json never lists a texture set whose TEX files
        # failed to convert.
        if progress_callback is not None:
            progress_callback(
                PublishProgressUpdate(
                    stage=PublishStage.WRITING_METADATA,
                    message="Writing material metadata for the published textures.",
                )
            )
        try:
            write_material_info(self._out_path, exp_setting_arr)
        except (OSError, ValueError) as exc:
            log.exception("Failed to write material info metadata.")
            raise TextureExportError(
                "Textures exported, but failed to write material metadata.\n"
                f"Details: {exc}"
            ) from exc

    def _move_previews(
        self, exported_textures: dict[tuple[str, str], list[str]]
    ) -> list[list[str]]:
        """Move the exported preview jpegs from `_src` into `_preview`."""
        render_sources: list[list[str]] = []
        for textures in exported_textures.values():
            kept: list[str] = []
            for texture in textures:
                path = Path(texture)
                if path.suffix.lower() == _PREVIEW_SUFFIX:
                    path.replace(self._preview_path / path.name)
                else:
                    kept.append(texture)
            render_sources.append(kept)
        return render_sources

    def _export_substance_textures(
        self,
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
        *,
        mat_var: str,
        geo_var: str,
        material_layer: str,
        progress_callback: PublishProgressCallback | None,
    ) -> dict[tuple[str, str], list[str]]:
        """Run the SP export. Emits one `texture.export.substance` event.

        Raises `TextureExportError` on any failure so the surrounding
        `record()` block records the right error code and message.
        """
        initial_payload = {
            "geo_variant": geo_var,
            "material_variant": mat_var,
            "renderman_variant": material_layer,
            "texture_set_count": len(exp_setting_arr),
            "udim_set_count": count_udim_sets(exp_setting_arr),
        }

        # Counts populated as work proceeds. The finally block at the bottom
        # emits one update() with whatever has been reached when the block
        # exits — success or failure both report partial progress, which the
        # dashboard needs to diagnose where in the export pipeline a failure
        # occurred.
        resolved_target_count = len(exp_setting_arr)
        udim_target_count = count_udim_sets(exp_setting_arr)
        planned_texture_count = 0
        all_exported_textures: dict[tuple[str, str], list[str]] = {}

        with telemetry.record(
            telemetry.EVENT_TEXTURE_EXPORT_SUBSTANCE,
            payload=initial_payload,
            asset=self._asset,
        ) as telemetry_event:
            try:
                self._init_paths(mat_var, geo_var, material_layer)
                log.info(f"Exporting textures to {self._out_path}")

                self._cleanup_export_lock(context="before export")

                try:
                    resolved_targets = resolve_export_targets(exp_setting_arr)
                except ValueError as exc:
                    raise TextureExportError(str(exc)) from exc

                resolved_target_count = len(resolved_targets)
                udim_target_count = count_udim_sets(
                    [target.settings for target in resolved_targets]
                )

                try:
                    planned_by_target = self._preflight_exports(
                        resolved_targets, progress_callback=progress_callback
                    )
                except ValueError as exc:
                    raise TextureExportError(str(exc)) from exc

                for target_index, target in enumerate(resolved_targets, start=1):
                    self._cleanup_export_lock(
                        context=f'before export for "{target.texture_set_name}"'
                    )
                    planned_exports = planned_by_target.get(target.texture_set_name, {})
                    try:
                        exported_textures = self._export_target(
                            target,
                            planned_exports=planned_exports,
                            target_index=target_index,
                            target_count=len(resolved_targets),
                            progress_callback=progress_callback,
                        )
                    except RuntimeError as exc:
                        log.error(
                            f'Texture export failed while processing texture set "{target.texture_set_name}".'
                        )
                        raise TextureExportError(str(exc)) from exc

                    planned_texture_count += _file_count(planned_exports)
                    all_exported_textures.update(exported_textures)
                    QtWidgets.QApplication.processEvents()

                return all_exported_textures
            finally:
                telemetry_event.update(
                    texture_set_count=resolved_target_count,
                    udim_set_count=udim_target_count,
                    planned_texture_count=planned_texture_count,
                    exported_texture_count=_file_count(all_exported_textures),
                )
