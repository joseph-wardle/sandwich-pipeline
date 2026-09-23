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

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, NotRequired, TypedDict

import hou
from Qt import QtWidgets

from pipe.core.asset import asset_owner_from_metadata
from pipe.core.ui.progress import progress_scope
from pipe.dcc.houdini.gallery import SESSION_DB_ENV, production_db_path, thumbnail_path
from pipe.dcc.houdini.gallery import db as gallery_db
from pipe.dcc.houdini.gallery import thumbnail as gallery_thumbnail
from pipe.core.versioning import (
    DCC_HOUDINI,
    VERSION_MANIFEST_FILENAME,
    backup_file,
    get_manifest_path,
    next_version,
    record_publish,
    stream_key_for,
)

from . import hooks as publish_hooks

log = logging.getLogger(__name__)

COMPONENT_OUTPUT_TYPE_NAME = "componentoutput"
EXPORT_PARMS = ("execute", "render", "renderbutton")
REBUILD_COMMAND = "pipe houdini -p -m pipe.dcc.houdini.gallery.rebuild"
MANIFEST_FILENAME = VERSION_MANIFEST_FILENAME
DEFAULT_VARIANT = "main"


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
    thumbnail_file: str
    thumbnail_bytes: int


class GallerySummary(TypedDict):
    status: str
    db_path: str
    item_id: str


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
    geo_variant: str | None = None
    material_variant: str | None = None
    material_layer: str | None = None

    save_hip_before_publish: bool = True
    backup_dir: Path | None = None
    manifest_path: Path | None = None
    backup_stem: str | None = None
    backup_ext: str | None = None
    title: str | None = None
    note: str | None = None
    # Backward-compatible alias used by existing HDA parameters.
    publish_note: str | None = None
    tool_version: str | None = None

    export_component: bool = True

    render_thumbnail: bool = True

    update_gallery: bool = True
    # Set only to publish into a DB other than production and the session copy.
    gallery_db_path: Path | None = None
    gallery_label: str | None = None
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
    geo_variant: str
    material_variant: str
    material_layer: str
    export_path: Path


_PUBLISH_STEPS = [
    "Backing up project",
    "Exporting component",
    "Rendering thumbnail",
    "Syncing gallery",
    "Running hooks",
]


def publish_component(
    node_path: str,
    options: PublishOptions | Mapping[str, Any] | None = None,
    *,
    parent: QtWidgets.QWidget | None = None,
) -> PublishResult:
    """Publish a component output node with a reproducible backup snapshot."""
    opts = _coerce_options(options)
    result = _new_result(node_path=node_path, variant=_normalized_variant(opts.variant))
    try:
        context = _preflight_context(node_path=node_path, options=opts, result=result)
        if context is None:
            return _finalize_result(result)

        with progress_scope(
            parent=parent,
            title="Publishing Component",
            steps=_PUBLISH_STEPS,
        ) as progress:
            progress.begin_step("Backing up project")
            backup = _backup_snapshot(context=context, options=opts, result=result)
            if backup is None:
                return _finalize_result(result)

            progress.begin_step("Exporting component", "This may take a moment...")
            export = _export_component(context=context, options=opts, result=result)
            if export is None:
                return _finalize_result(result)

            progress.begin_step("Rendering thumbnail")
            thumbnail, thumbnail_bytes = _render_thumbnail(
                context=context, options=opts, result=result
            )
            result["thumbnail"] = thumbnail

            progress.begin_step("Syncing gallery")
            gallery = _sync_gallery(
                context=context,
                options=opts,
                result=result,
                thumbnail_bytes=thumbnail_bytes,
            )
            result["gallery"] = gallery

            progress.begin_step("Running hooks")
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
    return {"captured": False, "thumbnail_file": "", "thumbnail_bytes": 0}


def _default_gallery_summary() -> GallerySummary:
    return {"status": "skipped", "db_path": "", "item_id": ""}


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
    geo_variant = _normalized_optional(
        options.geo_variant or options.variant or _safe_context_option("GEO_VARIANT")
    )
    material_variant = _normalized_optional(
        options.material_variant or _safe_context_option("MAT_VARIANT")
    )
    material_layer = _normalized_optional(
        options.material_layer or _safe_context_option("MATERIAL_LAYER")
    )

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
        geo_variant=geo_variant,
        material_variant=material_variant,
        material_layer=material_layer,
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
        stream_label = f"{backup_stem}.{backup_ext}"
        record_publish(
            context.manifest_path,
            dcc=DCC_HOUDINI,
            stream_key=stream_key_for(DCC_HOUDINI, backup_stem, backup_ext),
            stem=backup_stem,
            ext=backup_ext,
            stream_label=stream_label,
            working_path=context.hip_path,
            source_path=context.hip_path,
            backup_path=backup_path,
            version=version,
            title=options.title,
            context="publish",
            note=options.note or options.publish_note,
            tool_version=options.tool_version,
            owner=asset_owner_from_metadata(
                display_name=context.asset_name,
                asset_path=options.asset_path,
                asset_id=options.asset_id,
            ),
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
        for parm_name in EXPORT_PARMS:
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


def _render_thumbnail(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
) -> tuple[ThumbnailSummary, bytes | None]:
    summary = _default_thumbnail_summary()
    if not options.render_thumbnail:
        return summary, None

    target = thumbnail_path(context.export_path)
    summary["thumbnail_file"] = str(target)
    try:
        data = gallery_thumbnail.render(context.export_path, target)
    except gallery_thumbnail.ThumbnailRenderError as exc:
        _warn(
            result,
            "ThumbnailRenderFailed",
            f"Could not render a gallery thumbnail for {context.asset_name}; "
            f"the gallery keeps the previous one. {exc}",
        )
        return summary, None

    summary["captured"] = True
    summary["thumbnail_bytes"] = len(data)
    return summary, data


def _sync_gallery(
    *,
    context: _PublishContext,
    options: PublishOptions,
    result: PublishResult,
    thumbnail_bytes: bytes | None,
) -> GallerySummary:
    summary = _default_gallery_summary()
    if not options.update_gallery:
        return summary

    label = options.gallery_label or context.asset_name
    db_path = options.gallery_db_path or production_db_path()
    summary["db_path"] = str(db_path)
    try:
        summary["item_id"] = gallery_db.upsert_item(
            db_path,
            label=label,
            file_path=context.export_path,
            thumbnail=thumbnail_bytes,
        )
    except Exception as exc:
        summary["status"] = "failed"
        _gallery_issue(
            result,
            options,
            code="GallerySyncFailed",
            message=(
                f"{label} is published, but the Asset Gallery at {db_path} could "
                f"not be updated: {exc}. Run "
                f"`{REBUILD_COMMAND} --only {context.asset_root.name}` to add it."
            ),
        )
        return summary

    summary["status"] = "success"
    if options.gallery_db_path is None:
        _refresh_session_gallery(
            context, result, label=label, thumbnail_bytes=thumbnail_bytes
        )
    return summary


def _refresh_session_gallery(
    context: _PublishContext,
    result: PublishResult,
    *,
    label: str,
    thumbnail_bytes: bytes | None,
) -> None:
    """Upsert into the private copy this session has open and make the panel
    re-read it, so the artist sees the item without relaunching."""
    session = (os.getenv(SESSION_DB_ENV) or "").strip()
    if not session or Path(session) == production_db_path():
        return
    try:
        gallery_db.upsert_item(
            Path(session),
            label=label,
            file_path=context.export_path,
            thumbnail=thumbnail_bytes,
        )
        if hou.isUIAvailable():
            hou.ui.reloadSharedLayoutDataSource()
    except Exception as exc:
        _warn(
            result,
            "SessionGalleryStale",
            f"{label} is in the production Asset Gallery, but this session's "
            f"panel could not be refreshed: {exc}. Relaunch Houdini to see it.",
        )


def _node_can_export(node: hou.LopNode) -> bool:
    return any(node.parm(parm_name) is not None for parm_name in EXPORT_PARMS)


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

    published_at = _utc_now_iso()
    hook_context = {
        "asset_name": context.asset_name,
        "asset_root": str(context.asset_root),
        "variant": context.variant,
        "geo_variant": context.geo_variant,
        "material_variant": context.material_variant,
        "material_layer": context.material_layer,
        "node_path": context.node.path(),
        "hip_path": str(context.hip_path),
        "manifest_path": str(context.manifest_path),
        "backup_hip": backup["backup_hip"],
        "backup_version": str(backup["backup_version"]),
        "export_path": export["export_path"],
        "export_executed": str(export["executed"]),
        "thumbnail_file": result["thumbnail"].get("thumbnail_file", ""),
        "gallery_item_id": gallery["item_id"],
        "gallery_status": gallery["status"],
        "published_at": published_at,
    }

    for spec in options.hooks:
        execution = publish_hooks.execute_hook(spec, hook_context)
        hook_summary: HookSummary = {
            "hook": execution["hook"],
            "status": execution["status"],
            "message": execution["message"],
        }
        if execution["payload"]:
            hook_summary["payload"] = dict(execution["payload"])
        summaries.append(hook_summary)

        if execution["status"] == "failed":
            message = f"Hook failed ({spec}): {execution['message']}"
            if options.fail_on_hook_error:
                _error(result, "HookFailed", message)
            else:
                _warn(result, "HookFailed", message)
            continue

        if execution["status"] == "skipped":
            log.info("Hook skipped (%s): %s", execution["hook"], execution["message"])
            continue

        log.info("Hook succeeded (%s): %s", execution["hook"], execution["message"])
    return summaries


def _normalized_variant(value: str | None) -> str:
    text = (value or "").strip()
    return text or DEFAULT_VARIANT


def _normalized_optional(value: str | None) -> str:
    text = (value or "").strip()
    return text


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
