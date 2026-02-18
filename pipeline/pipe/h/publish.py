"""Reusable Houdini component publish service.

This module centralizes publish behavior so UI buttons and headless scripts
can call the same function:

    publish_component(node_path, options)

Design goals:
1. Deterministic, structured results for machine and human consumers.
2. Always snapshot the current HIP file with a versioned backup before export.
3. Keep implementation small, explicit, and easy to grep/maintain.
"""

from __future__ import annotations

import importlib
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, NotRequired, TypedDict

import hou

from pipe.asset.versioning import (
    backup_file,
    get_manifest_path,
    next_version,
    record_publish,
)

log = logging.getLogger(__name__)

COMPONENT_OUTPUT_TYPE_NAME = "componentoutput"
DCC_HOUDINI_NAME = "houdini"
MANIFEST_FILENAME = "asset_manifest.json"
DEFAULT_VARIANT = "main"
DEFAULT_HOOK_FUNCTION = "run"
THUMBNAIL_CONTEXT_OPTION = "RENDER_THUMBNAIL"
THUMBNAIL_FALLBACK_MODE = 3

GALLERY_META_ASSET_KEY = "pipe_asset_key"
GALLERY_META_ASSET_NAME = "pipe_asset_name"
GALLERY_META_BACKUP_VERSION = "pipe_backup_version"
GALLERY_META_EXPORT_PATH = "pipe_export_path"
GALLERY_META_HIP_PATH = "pipe_hip_path"
GALLERY_META_NODE_PATH = "pipe_node_path"
GALLERY_META_PUBLISHED_AT = "pipe_published_at"
GALLERY_META_VARIANT = "pipe_variant"


class PublishMessage(TypedDict):
    code: str
    message: str


class BackupSnapshot(TypedDict):
    source_hip: str
    backup_hip: str
    backup_version: int
    manifest_path: str


class ExportSummary(TypedDict):
    attempted: bool
    executed: bool
    method: str
    export_path: str


class ThumbnailSummary(TypedDict):
    captured: bool
    camera: str
    renderer: str
    mode: int
    thumbnail_file: str
    thumbnail_bytes: int


class GallerySummary(TypedDict):
    status: str
    db_path: str
    item_id: str
    pruned_item_ids: list[str]


class HookSummary(TypedDict):
    hook: str
    status: str
    message: str
    payload: NotRequired[dict[str, str]]


class PublishResult(TypedDict):
    status: str
    node_path: str
    node_type: str
    hip_path: str
    asset_root: str
    variant: str
    backup: BackupSnapshot | None
    export: ExportSummary | None
    thumbnail: ThumbnailSummary
    gallery: GallerySummary
    hooks: list[HookSummary]
    warnings: list[PublishMessage]
    errors: list[PublishMessage]


@dataclass(slots=True)
class PublishOptions:
    """Options for publish_component.

    Keep this intentionally small and explicit. This object is safe to construct
    from a dict using PublishOptions.from_mapping.
    """

    asset_root: Path | None = None
    asset_name: str | None = None
    asset_path: str | None = None
    asset_id: int | None = None
    variant: str | None = None

    save_hip_before_publish: bool = True
    backup_dir: Path | None = None
    manifest_path: Path | None = None
    backup_stem: str | None = None
    backup_ext: str | None = None
    publish_note: str | None = None
    tool_version: str | None = None

    export_component: bool = True

    collect_thumbnail: bool = True
    generate_thumbnail_if_missing: bool = True
    thumbnail_max_bytes: int = 8 * 1024 * 1024

    update_gallery: bool = True
    gallery_db_path: Path | None = None
    gallery_label: str | None = None
    prune_existing_items: bool = True
    fail_on_gallery_error: bool = False

    hooks: tuple[str, ...] = ()
    fail_on_hook_error: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> PublishOptions:
        values = dict(data)
        for key in ("asset_root", "backup_dir", "manifest_path", "gallery_db_path"):
            if key in values and values[key] is not None:
                values[key] = Path(str(values[key]))
        if "asset_id" in values and values["asset_id"] is not None:
            values["asset_id"] = int(values["asset_id"])
        if "hooks" in values and values["hooks"] is not None:
            values["hooks"] = tuple(str(spec) for spec in values["hooks"])
        return cls(**values)


@dataclass(slots=True)
class _PublishContext:
    node: hou.LopNode
    hip_path: Path
    asset_root: Path
    manifest_path: Path
    backup_dir: Path
    asset_name: str
    variant: str
    export_path: Path


def publish_component(
    node_path: str, options: PublishOptions | Mapping[str, Any] | None = None
) -> PublishResult:
    """Publish a component output node with a reproducible backup snapshot."""
    opts = _coerce_options(options)
    result = _new_result(node_path=node_path, variant=_normalized_variant(opts.variant))
    try:
        context = _preflight_context(node_path=node_path, options=opts, result=result)
        if context is None:
            return _finalize_result(result)

        backup = _backup_snapshot(context=context, options=opts, result=result)
        if backup is None:
            return _finalize_result(result)

        export = _export_component(context=context, options=opts, result=result)
        if export is None:
            return _finalize_result(result)

        thumbnail, thumbnail_bytes = _collect_thumbnail(
            context=context, options=opts, result=result
        )
        result["thumbnail"] = thumbnail

        gallery = _sync_gallery(
            context=context,
            options=opts,
            result=result,
            thumbnail_bytes=thumbnail_bytes,
            backup_version=backup["backup_version"],
        )
        result["gallery"] = gallery

        hooks = _run_hooks(
            context=context,
            options=opts,
            result=result,
            backup=backup,
            export=export,
            gallery=gallery,
        )
        result["hooks"] = hooks
        result["backup"] = backup
        result["export"] = export
    except Exception as exc:
        _error(result, "UnhandledPublishException", str(exc))
    return _finalize_result(result)


def _coerce_options(
    options: PublishOptions | Mapping[str, Any] | None,
) -> PublishOptions:
    if options is None:
        return PublishOptions()
    if isinstance(options, PublishOptions):
        return options
    return PublishOptions.from_mapping(options)


def _new_result(node_path: str, variant: str) -> PublishResult:
    return {
        "status": "failed",
        "node_path": node_path,
        "node_type": "",
        "hip_path": "",
        "asset_root": "",
        "variant": variant,
        "backup": None,
        "export": None,
        "thumbnail": _default_thumbnail_summary(),
        "gallery": _default_gallery_summary(),
        "hooks": [],
        "warnings": [],
        "errors": [],
    }


def _default_thumbnail_summary() -> ThumbnailSummary:
    return {
        "captured": False,
        "camera": "",
        "renderer": "",
        "mode": -1,
        "thumbnail_file": "",
        "thumbnail_bytes": 0,
    }


def _default_gallery_summary() -> GallerySummary:
    return {"status": "skipped", "db_path": "", "item_id": "", "pruned_item_ids": []}


def _preflight_context(
    *,
    node_path: str,
    options: PublishOptions,
    result: PublishResult,
) -> _PublishContext | None:
    node = _resolve_component_output_node(node_path=node_path, result=result)
    if node is None:
        return None

    hip_path = _resolve_hip_path(node=node, options=options, result=result)
    if hip_path is None:
        return None

    export_path = _resolve_export_path(node=node, hip_path=hip_path, result=result)
    if export_path is None:
        return None

    asset_root = _resolve_asset_root(hip_path=hip_path, options=options)
    manifest_path = (options.manifest_path or get_manifest_path(asset_root)).resolve()
    backup_dir = (options.backup_dir or (asset_root / ".backup")).resolve()
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _error(
            result,
            "BackupDirectoryError",
            f"Failed to create backup directory {backup_dir}: {exc}",
        )
        return None

    asset_name = _resolve_asset_name(options=options, asset_root=asset_root)
    variant = _normalized_variant(options.variant)

    if options.export_component and not _node_can_export(node):
        _error(
            result,
            "ExportTriggerMissing",
            f"No supported export trigger found on {node.path()}",
        )
        return None

    result["node_type"] = node.type().name()
    result["hip_path"] = str(hip_path)
    result["asset_root"] = str(asset_root)
    result["variant"] = variant

    return _PublishContext(
        node=node,
        hip_path=hip_path,
        asset_root=asset_root,
        manifest_path=manifest_path,
        backup_dir=backup_dir,
        asset_name=asset_name,
        variant=variant,
        export_path=export_path,
    )


def _resolve_component_output_node(
    *, node_path: str, result: PublishResult
) -> hou.LopNode | None:
    node = hou.node(node_path)
    if node is None:
        _error(result, "NodeNotFound", f"Node not found: {node_path}")
        return None
    if not isinstance(node, hou.LopNode):
        _error(result, "NodeTypeError", f"Node is not a LOP node: {node_path}")
        return None

    if node.type().name() == COMPONENT_OUTPUT_TYPE_NAME:
        return node

    matches = _embedded_component_outputs(node)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        _error(
            result,
            "AmbiguousComponentOutput",
            f"Node contains multiple componentoutput children: {node.path()}",
        )
        return None

    _error(
        result,
        "ComponentOutputRequired",
        f"Node is not a componentoutput and has no componentoutput child: {node.path()}",
    )
    return None


def _embedded_component_outputs(node: hou.Node) -> list[hou.LopNode]:
    """Return embedded componentoutput children for wrapper HDAs.

    Some locked HDAs do not expose internal nodes through `children()`, but
    still resolve direct paths via `node.node("component_output")`.
    """

    matches: list[hou.LopNode] = []
    seen_paths: set[str] = set()

    def _append(candidate: hou.Node | None) -> None:
        if candidate is None:
            return
        if not isinstance(candidate, hou.LopNode):
            return
        if candidate.type().name() != COMPONENT_OUTPUT_TYPE_NAME:
            return
        path = candidate.path()
        if path in seen_paths:
            return
        seen_paths.add(path)
        matches.append(candidate)

    # Most common direct internal names for wrapper HDAs.
    for name in ("component_output", "componentoutput", "COMPONENT_OUT"):
        _append(node.node(name))

    # Visible direct children (unlocked HDAs / subnets).
    for child in node.children():
        if child.parent() != node:
            continue
        _append(child)

    if matches:
        return matches

    # Locked HDAs can still report descendants via allSubChildren.
    try:
        descendants = node.allSubChildren()
    except Exception:
        descendants = ()
    for child in descendants:
        if child.parent() != node:
            continue
        _append(child)
    return matches


def _resolve_hip_path(
    *, node: hou.LopNode, options: PublishOptions, result: PublishResult
) -> Path | None:
    if options.save_hip_before_publish:
        try:
            hou.hipFile.save()
        except Exception as exc:
            _error(result, "HipSaveFailed", f"Failed to save HIP before publish: {exc}")
            return None

    hip_str = (hou.hipFile.path() or "").strip()
    if not hip_str:
        _error(result, "HipPathMissing", "Current HIP file has no path.")
        return None

    hip_path = Path(hou.expandString(hip_str)).expanduser()
    if not hip_path.is_absolute():
        hip_path = (Path(hou.hscriptStringExpression("$HIP")) / hip_path).resolve()
    else:
        hip_path = hip_path.resolve()

    if not hip_path.exists():
        _error(
            result,
            "HipFileMissing",
            f"HIP file does not exist on disk: {hip_path}",
        )
        return None
    if not hip_path.is_file():
        _error(result, "HipPathInvalid", f"HIP path is not a file: {hip_path}")
        return None

    if node.parm("lopoutput") is None:
        _error(
            result,
            "MissingParm",
            f"Node {node.path()} is missing required parm 'lopoutput'.",
        )
        return None
    return hip_path


def _resolve_export_path(
    *, node: hou.LopNode, hip_path: Path, result: PublishResult
) -> Path | None:
    parm = node.parm("lopoutput")
    if parm is None:
        _error(
            result,
            "MissingParm",
            f"Node {node.path()} is missing required parm 'lopoutput'.",
        )
        return None

    export_value = ""
    try:
        export_value = parm.evalAsString().strip()
    except Exception:
        pass

    if not export_value:
        try:
            export_value = hou.expandString(parm.unexpandedString()).strip()
        except Exception:
            pass

    if not export_value:
        _error(
            result,
            "InvalidExportPath",
            f"Unable to evaluate lopoutput on {node.path()}",
        )
        return None

    export_path = Path(export_value).expanduser()
    if not export_path.is_absolute():
        export_path = (hip_path.parent / export_path).resolve()
    else:
        export_path = export_path.resolve()
    return export_path


def _resolve_asset_root(*, hip_path: Path, options: PublishOptions) -> Path:
    if options.asset_root:
        return options.asset_root.expanduser().resolve()

    for parent in [hip_path.parent, *hip_path.parents]:
        if (parent / MANIFEST_FILENAME).exists():
            return parent.resolve()
        if (parent / "publish").is_dir():
            return parent.resolve()
    return hip_path.parent.resolve()


def _resolve_asset_name(*, options: PublishOptions, asset_root: Path) -> str:
    if options.asset_name and options.asset_name.strip():
        return options.asset_name.strip()
    if value := _safe_context_option("ASSET"):
        return value
    return asset_root.name


def _backup_snapshot(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
) -> BackupSnapshot | None:
    backup_stem = options.backup_stem or context.hip_path.stem
    backup_ext = (options.backup_ext or context.hip_path.suffix.lstrip(".")) or "hip"

    version = next_version(context.backup_dir, backup_stem, backup_ext)
    backup_path = backup_file(
        context.hip_path,
        context.backup_dir,
        stem=backup_stem,
        ext=backup_ext,
        version=version,
        ensure_exists=True,
    )
    if backup_path is None:
        _error(
            result,
            "BackupFailed",
            f"Failed to create HIP backup for {context.hip_path}",
        )
        return None

    try:
        record_publish(
            context.manifest_path,
            dcc=DCC_HOUDINI_NAME,
            source_path=context.hip_path,
            backup_path=backup_path,
            version=version,
            note=options.publish_note,
            tool_version=options.tool_version,
            asset_name=context.asset_name,
            asset_path=options.asset_path,
            asset_id=options.asset_id,
            extra={
                "variant": context.variant,
                "publish_node": context.node.path(),
                "export_path": str(context.export_path),
                "snapshot_policy": "always",
            },
        )
    except Exception as exc:
        _error(
            result,
            "ManifestWriteFailed",
            f"Failed to record publish in manifest {context.manifest_path}: {exc}",
        )
        return None

    return {
        "source_hip": str(context.hip_path),
        "backup_hip": str(backup_path),
        "backup_version": version,
        "manifest_path": str(context.manifest_path),
    }


def _export_component(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
) -> ExportSummary | None:
    if not options.export_component:
        return {
            "attempted": False,
            "executed": False,
            "method": "skipped",
            "export_path": str(context.export_path),
        }

    context.export_path.parent.mkdir(parents=True, exist_ok=True)

    node = context.node
    previous_errors = tuple(node.errors())
    executed = False
    method = "none"

    try:
        if hasattr(node, "saveToDisk") and callable(node.saveToDisk):
            if node.saveToDisk():
                executed = True
                method = "saveToDisk"
        if not executed:
            for parm_name in ("execute", "render", "renderbutton"):
                parm = node.parm(parm_name)
                if parm is None:
                    continue
                parm.pressButton()
                executed = True
                method = parm_name
                break
    except Exception as exc:
        _error(
            result,
            "ExportExecutionError",
            f"Failed to execute component export on {node.path()}: {exc}",
        )
        return None

    if not executed:
        _error(
            result,
            "ExportTriggerMissing",
            f"No supported export trigger found on {node.path()}",
        )
        return None

    new_errors = [err for err in node.errors() if err not in previous_errors]
    if new_errors:
        _error(
            result,
            "ExportNodeError",
            "Component Output reported errors after export: " + "; ".join(new_errors),
        )
        return None

    if not context.export_path.exists():
        _warn(
            result,
            "ExportPathMissingAfterExport",
            f"Export executed, but output file is missing: {context.export_path}",
        )

    return {
        "attempted": True,
        "executed": True,
        "method": method,
        "export_path": str(context.export_path),
    }


def _collect_thumbnail(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
) -> tuple[ThumbnailSummary, bytes | None]:
    node = context.node
    summary = _default_thumbnail_summary()
    summary["camera"] = _eval_parm_string(node, "thumbnailinputcamera")
    summary["renderer"] = _eval_parm_string(node, "renderer")
    summary["mode"] = _eval_parm_int(node, "thumbnailmode", default=-1)

    thumbnail_path = _resolve_thumbnail_output_path(node)
    if options.collect_thumbnail and options.generate_thumbnail_if_missing:
        # Regenerate thumbnail on every publish to keep gallery visuals current.
        with _thumbnail_context_enabled():
            _generate_thumbnail_if_supported(node=node, result=result)

        thumbnail_path = _resolve_thumbnail_output_path(node)
        if thumbnail_path is not None and not thumbnail_path.exists():
            with _thumbnail_context_enabled():
                _generate_thumbnail_if_supported(
                    node=node,
                    result=result,
                    force_mode=THUMBNAIL_FALLBACK_MODE,
                )
        thumbnail_path = _resolve_thumbnail_output_path(node)

    summary["thumbnail_file"] = str(thumbnail_path) if thumbnail_path else ""

    if not options.collect_thumbnail or thumbnail_path is None:
        return summary, None

    if not thumbnail_path.exists():
        _warn(
            result,
            "ThumbnailFileMissing",
            f"Thumbnail file path is set but file does not exist: {thumbnail_path}",
        )
        return summary, None

    source_path = thumbnail_path
    staged_path = _stage_thumbnail_in_publish_cache(
        source=source_path, context=context, result=result
    )
    thumbnail_path = staged_path or source_path
    _relocate_thumbnail_scene_artifacts(context=context, result=result)
    _cleanup_stock_thumbnail_artifacts(
        source=source_path, staged=thumbnail_path, context=context
    )
    summary["thumbnail_file"] = str(thumbnail_path)

    try:
        data = thumbnail_path.read_bytes()
    except Exception as exc:
        _warn(
            result,
            "ThumbnailReadFailed",
            f"Failed reading thumbnail file {thumbnail_path}: {exc}",
        )
        return summary, None

    if len(data) > options.thumbnail_max_bytes:
        _warn(
            result,
            "ThumbnailTooLarge",
            f"Thumbnail data is larger than {options.thumbnail_max_bytes} bytes; skipping gallery thumbnail payload.",
        )
        return summary, None

    summary["captured"] = True
    summary["thumbnail_bytes"] = len(data)
    return summary, data


def _generate_thumbnail_if_supported(
    *, node: hou.LopNode, result: PublishResult, force_mode: int | None = None
) -> bool:
    mode = (
        force_mode
        if force_mode is not None
        else _eval_parm_int(node, "thumbnailmode", default=-1)
    )
    mode_button = {
        0: "executefile",
        1: "executegl",
        2: "executerender",
        3: "executeviewport",
    }.get(mode)

    candidate_buttons: list[str] = []
    if mode_button:
        candidate_buttons.append(mode_button)
    candidate_buttons.extend(
        name
        for name in (
            "executerender",
            "executegl",
            "executeviewport",
            "executefile",
            "executesavethumbnail",
        )
        if name not in candidate_buttons
    )

    mode_parm = node.parm("thumbnailmode")
    original_mode = None
    mode_overridden = False
    if force_mode is not None and mode_parm is not None:
        try:
            original_mode = int(mode_parm.evalAsInt())
            if original_mode != force_mode:
                mode_parm.set(force_mode)
                mode_overridden = True
        except Exception as exc:
            _warn(
                result,
                "ThumbnailModeSetFailed",
                f"Failed to set thumbnailmode={force_mode} on {node.path()}: {exc}",
            )

    pressed = False
    for parm_name in candidate_buttons:
        parm = node.parm(parm_name)
        if parm is None:
            continue
        try:
            parm.pressButton()
            pressed = True
            break
        except Exception as exc:
            _warn(
                result,
                "ThumbnailGenerateFailed",
                f"Failed pressing {parm_name} on {node.path()}: {exc}",
            )

    if mode_overridden and mode_parm is not None and original_mode is not None:
        try:
            mode_parm.set(original_mode)
        except Exception:
            pass

    if not pressed:
        _warn(
            result,
            "ThumbnailGenerateUnsupported",
            f"Node {node.path()} does not expose a supported thumbnail generate button.",
        )
        return False
    return True


def _resolve_thumbnail_output_path(node: hou.LopNode) -> Path | None:
    candidates = _thumbnail_output_candidates(node)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def _stage_thumbnail_in_publish_cache(
    *, source: Path, context: _PublishContext, result: PublishResult
) -> Path | None:
    target_dir = context.export_path.parent / ".thumbnails"
    target = target_dir / f"{context.export_path.stem}.png"
    if source == target:
        return source
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    except Exception as exc:
        _warn(
            result,
            "ThumbnailStageFailed",
            f"Failed staging thumbnail {source} -> {target}: {exc}",
        )
        return None
    return target


def _cleanup_stock_thumbnail_artifacts(
    *, source: Path, staged: Path, context: _PublishContext
) -> None:
    publish_dir = context.export_path.parent
    for path in (
        publish_dir / "thumbnail.png",
        publish_dir / "Thumbnail.png",
    ):
        if path == staged:
            continue
        _remove_file_if_exists(path)

    if (
        source != staged
        and source.parent == publish_dir
        and source.name.lower() in ("thumbnail.png",)
    ):
        _remove_file_if_exists(source)


def _relocate_thumbnail_scene_artifacts(
    *, context: _PublishContext, result: PublishResult
) -> None:
    publish_dir = context.export_path.parent
    target_dir = publish_dir / ".thumbnails"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _warn(
            result,
            "ThumbnailSceneStageFailed",
            f"Failed creating thumbnail scene directory {target_dir}: {exc}",
        )
        return

    for source in (
        publish_dir / "Thumbnail.usda",
        publish_dir / "thumbnail.usda",
        publish_dir / "Thumbnail.usd",
        publish_dir / "thumbnail.usd",
    ):
        if not source.exists() or not source.is_file():
            continue
        target = target_dir / source.name
        if source == target:
            continue
        try:
            source.replace(target)
        except Exception as exc:
            _warn(
                result,
                "ThumbnailSceneStageFailed",
                f"Failed moving thumbnail scene {source} -> {target}: {exc}",
            )


def _remove_file_if_exists(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass


def _thumbnail_output_candidates(node: hou.LopNode) -> list[Path]:
    candidates: list[Path] = []

    parm_path = _eval_parm_path(node, "thumbnailfile")
    if parm_path is not None:
        candidates.append(parm_path)

    render_node = node.node("thumbnail_render")
    if isinstance(render_node, hou.Node):
        render_path = _eval_parm_path(render_node, "outputimage")
        if render_path is not None:
            candidates.append(render_path)

    # Preserve insertion order while removing duplicates.
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


@contextmanager
def _thumbnail_context_enabled():
    had_previous = False
    previous_value: Any = None
    try:
        previous_value = hou.contextOption(THUMBNAIL_CONTEXT_OPTION)
        had_previous = True
    except Exception:
        had_previous = False

    try:
        hou.setContextOption(THUMBNAIL_CONTEXT_OPTION, 1)
    except Exception:
        pass

    try:
        yield
    finally:
        if had_previous:
            try:
                hou.setContextOption(THUMBNAIL_CONTEXT_OPTION, previous_value)
            except Exception:
                pass
        else:
            remove_fn = getattr(hou, "removeContextOption", None)
            if callable(remove_fn):
                try:
                    remove_fn(THUMBNAIL_CONTEXT_OPTION)
                except Exception:
                    pass


def _sync_gallery(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
    thumbnail_bytes: bytes | None,
    backup_version: int,
) -> GallerySummary:
    if not options.update_gallery:
        return _default_gallery_summary()

    db_path = _resolve_gallery_db_path(options)
    if db_path is None:
        message = "Gallery DB path is not configured (HOUDINI_ASSETGALLERY_DATA_SOURCE/HOUDINI_ASSETGALLERY_DB_FILE)."
        _gallery_issue(result, options, code="GalleryDBMissing", message=message)
        return _default_gallery_summary()

    try:
        datasource = hou.AssetGalleryDataSource(str(db_path))
    except Exception as exc:
        _gallery_issue(
            result,
            options,
            code="GalleryInitError",
            message=f"Failed to open Asset Gallery datasource {db_path}: {exc}",
        )
        return {
            "status": "failed",
            "db_path": str(db_path),
            "item_id": "",
            "pruned_item_ids": [],
        }

    if not datasource.isValid():
        _gallery_issue(
            result,
            options,
            code="GalleryInvalid",
            message=f"Asset Gallery datasource is invalid: {db_path}",
        )
        return {
            "status": "failed",
            "db_path": str(db_path),
            "item_id": "",
            "pruned_item_ids": [],
        }

    if datasource.isReadOnly():
        _gallery_issue(
            result,
            options,
            code="GalleryReadOnly",
            message=f"Asset Gallery datasource is read-only: {db_path}",
        )
        return {
            "status": "failed",
            "db_path": str(db_path),
            "item_id": "",
            "pruned_item_ids": [],
        }

    label = options.gallery_label or context.asset_name or context.export_path.stem
    asset_key = f"{context.asset_name}|{context.variant}"
    metadata = {
        GALLERY_META_ASSET_KEY: asset_key,
        GALLERY_META_ASSET_NAME: context.asset_name,
        GALLERY_META_VARIANT: context.variant,
        GALLERY_META_NODE_PATH: context.node.path(),
        GALLERY_META_HIP_PATH: str(context.hip_path),
        GALLERY_META_EXPORT_PATH: str(context.export_path),
        GALLERY_META_BACKUP_VERSION: str(backup_version),
        GALLERY_META_PUBLISHED_AT: _utc_now_iso(),
    }

    pruned_ids: list[str] = []
    added_item_id = ""
    try:
        existing_ids = _find_gallery_matches(
            datasource=datasource,
            export_path=context.export_path,
            asset_key=asset_key,
        )
        datasource.startTransaction()
        try:
            if options.prune_existing_items and existing_ids:
                if datasource.markItemsForDeletion(tuple(existing_ids)):
                    pruned_ids = existing_ids
                else:
                    _warn(
                        result,
                        "GalleryPruneFailed",
                        f"Failed to mark existing gallery items for deletion: {existing_ids}",
                    )

            added_item_id = datasource.addItem(label, str(context.export_path))
            if not added_item_id:
                raise RuntimeError("addItem returned an empty item id")

            datasource.setOwnsFile(added_item_id, False)
            datasource.setMetadata(added_item_id, metadata)
            if thumbnail_bytes:
                set_result = datasource.setThumbnail(added_item_id, thumbnail_bytes)
                if set_result is False:
                    _warn(
                        result,
                        "GalleryThumbnailSetFailed",
                        f"Asset Gallery rejected thumbnail bytes for item {added_item_id}.",
                    )
            elif options.collect_thumbnail:
                _warn(
                    result,
                    "GalleryThumbnailMissing",
                    f"No thumbnail bytes available for {context.node.path()}; gallery item will be created without a thumbnail.",
                )
        except Exception:
            datasource.endTransaction(commit=False)
            raise
        datasource.endTransaction(commit=True)
    except Exception as exc:
        _gallery_issue(
            result,
            options,
            code="GallerySyncFailed",
            message=f"Failed to sync publish to Asset Gallery: {exc}",
        )
        return {
            "status": "failed",
            "db_path": str(db_path),
            "item_id": "",
            "pruned_item_ids": pruned_ids,
        }

    return {
        "status": "success",
        "db_path": str(db_path),
        "item_id": added_item_id,
        "pruned_item_ids": pruned_ids,
    }


def _find_gallery_matches(
    *, datasource: hou.AssetGalleryDataSource, export_path: Path, asset_key: str
) -> list[str]:
    matches: list[str] = []
    export_str = str(export_path)
    for item_id in datasource.itemIds():
        item_path = datasource.filePath(item_id)
        if item_path == export_str:
            matches.append(item_id)
            continue
        metadata = datasource.metadata(item_id) or {}
        if str(metadata.get(GALLERY_META_ASSET_KEY, "")).strip() == asset_key:
            matches.append(item_id)
    return matches


def _node_can_export(node: hou.LopNode) -> bool:
    if hasattr(node, "saveToDisk") and callable(node.saveToDisk):
        return True
    return any(
        node.parm(parm_name) is not None
        for parm_name in ("execute", "render", "renderbutton")
    )


def _run_hooks(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
    backup: BackupSnapshot,
    export: ExportSummary,
    gallery: GallerySummary,
) -> list[HookSummary]:
    summaries: list[HookSummary] = []
    if not options.hooks:
        return summaries

    hook_context = {
        "asset_name": context.asset_name,
        "asset_root": str(context.asset_root),
        "variant": context.variant,
        "node_path": context.node.path(),
        "hip_path": str(context.hip_path),
        "manifest_path": str(context.manifest_path),
        "backup_hip": backup["backup_hip"],
        "backup_version": str(backup["backup_version"]),
        "export_path": export["export_path"],
        "gallery_item_id": gallery["item_id"],
        "gallery_status": gallery["status"],
    }

    for spec in options.hooks:
        try:
            callback = _resolve_hook(spec)
            payload = callback(dict(hook_context))
            hook_summary: HookSummary = {
                "hook": spec,
                "status": "success",
                "message": "ok",
            }
            if isinstance(payload, dict):
                hook_summary["payload"] = {
                    str(k): str(v) for k, v in payload.items() if v is not None
                }
            summaries.append(hook_summary)
        except Exception as exc:
            message = f"Hook failed ({spec}): {exc}"
            if options.fail_on_hook_error:
                _error(result, "HookFailed", message)
            else:
                _warn(result, "HookFailed", message)
            summaries.append({"hook": spec, "status": "failed", "message": str(exc)})
    return summaries


def _resolve_hook(spec: str) -> Callable[[dict[str, str]], Any]:
    module_name, attr_name = _parse_hook_spec(spec)
    module = importlib.import_module(module_name)
    callback = getattr(module, attr_name)
    if not callable(callback):
        raise TypeError(f"Hook is not callable: {spec}")
    return callback


def _parse_hook_spec(spec: str) -> tuple[str, str]:
    value = spec.strip()
    if ":" in value:
        module_name, attr_name = value.split(":", 1)
        return module_name.strip(), attr_name.strip()

    # Prefer importing as a module first (default function `run`).
    try:
        importlib.import_module(value)
        return value, DEFAULT_HOOK_FUNCTION
    except Exception:
        pass

    module_name, sep, attr_name = value.rpartition(".")
    if not sep:
        raise ValueError(
            f"Invalid hook spec '{spec}'. Use 'module', 'module:function', or 'module.function'."
        )
    return module_name.strip(), attr_name.strip()


def _resolve_gallery_db_path(options: PublishOptions) -> Path | None:
    if options.gallery_db_path:
        return options.gallery_db_path.expanduser().resolve()

    for env_name in (
        "HOUDINI_ASSETGALLERY_DATA_SOURCE",
        "HOUDINI_ASSETGALLERY_DB_FILE",
    ):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return None


def _eval_parm_string(node: hou.Node, parm_name: str) -> str:
    parm = node.parm(parm_name)
    if parm is None:
        return ""
    try:
        return parm.evalAsString().strip()
    except Exception:
        return ""


def _eval_parm_int(node: hou.Node, parm_name: str, *, default: int) -> int:
    parm = node.parm(parm_name)
    if parm is None:
        return default
    try:
        return int(parm.evalAsInt())
    except Exception:
        return default


def _eval_parm_path(node: hou.Node, parm_name: str) -> Path | None:
    value = _eval_parm_string(node, parm_name)
    if not value:
        return None
    return Path(hou.expandString(value)).expanduser().resolve()


def _normalized_variant(value: str | None) -> str:
    text = (value or "").strip()
    return text or DEFAULT_VARIANT


def _safe_context_option(name: str) -> str | None:
    try:
        value = hou.contextOption(name)
    except Exception:
        return None
    text = str(value).strip() if value is not None else ""
    return text or None


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _gallery_issue(
    result: PublishResult, options: PublishOptions, *, code: str, message: str
) -> None:
    if options.fail_on_gallery_error:
        _error(result, code, message)
    else:
        _warn(result, code, message)


def _warn(result: PublishResult, code: str, message: str) -> None:
    result["warnings"].append({"code": code, "message": message})
    log.warning("%s: %s", code, message)


def _error(result: PublishResult, code: str, message: str) -> None:
    result["errors"].append({"code": code, "message": message})
    log.error("%s: %s", code, message)


def _finalize_result(result: PublishResult) -> PublishResult:
    result["status"] = "failed" if result["errors"] else "success"
    return result


__all__ = [
    "PublishOptions",
    "PublishResult",
    "publish_component",
]
