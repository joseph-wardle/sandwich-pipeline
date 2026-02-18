"""
Callbacks for the SKD Component Output HDA.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import hou

from pipe.h.publish import PublishOptions, publish_component

TURNAROUND_HOOK = "pipe.h.publish_hooks.turnaround:run"
TURNAROUND_SG_HOOK = "pipe.h.publish_hooks.turnaround_shotgrid:run"

STATUS_SUMMARY_PARM = "status_summary"
STATUS_JSON_PARM = "status_json"
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_BROKEN_NAME_TOKENS = ('chs("name")', "chs('name')")
_BROKEN_LOPOUTPUT_TOKENS = (
    '$HIP/usd/assets/`chs("name")`/`chs("filename")`',
    "$HIP/usd/assets/`chs('name')`/`chs('filename')`",
    "/usd/assets/",
)
_DEFAULT_LOPOUTPUT = '$HIP/publish/`chs("filename")`'


def on_created(node: hou.Node) -> None:
    """Initialize defaults for the wrapper and internal componentoutput node."""
    _repair_broken_output_paths(node)

    asset_name = _default_asset_name()
    filename = _default_component_filename(asset_name=asset_name)
    core_defaults: dict[str, Any] = {
        "filename": filename,
        "rootprim": f"/{asset_name}",
        "localize": 0,
        "lopoutput": _DEFAULT_LOPOUTPUT,
        "thumbnailmode": 2,
        "renderer": "RenderMan RIS",
        "thumbnailscenesource": 1,
        "thumbnailinputcamera": "/lookdev/cam",
    }

    # Prefer setting promoted wrapper parms so internal channel references stay intact.
    for parm_name, value in core_defaults.items():
        _set_if_exists(node, parm_name, value)

    comp = _find_component_output(node)
    if comp is not None:
        _set_if_exists(comp, "addtogallery", 0)
        # Also apply defaults directly for non-promoted or non-referenced setups.
        for parm_name, value in core_defaults.items():
            if node.parm(parm_name) is None:
                _set_if_exists(comp, parm_name, value)

    _set_if_exists(node, "tool_version", "SKD_ComponentOutput.1.0")
    _write_status(
        node,
        title="Ready",
        payload={"status": "ready", "warnings": [], "errors": []},
    )


def preflight(node: hou.Node) -> dict[str, Any]:
    """Run lightweight preflight checks before publish."""
    _repair_broken_output_paths(node)

    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    checks: list[str] = []

    comp = _find_component_output(node)
    if comp is None:
        errors.append(
            {
                "code": "ComponentOutputMissing",
                "message": "No internal componentoutput node found.",
            }
        )
    else:
        checks.append(f"componentoutput: {comp.path()}")

        hip_path = (hou.hipFile.path() or "").strip()
        if not hip_path:
            errors.append(
                {"code": "HipPathMissing", "message": "Current HIP has no file path."}
            )
        else:
            expanded = Path(hou.expandString(hip_path)).expanduser()
            if not expanded.exists():
                errors.append(
                    {
                        "code": "HipFileMissing",
                        "message": f"HIP file does not exist: {expanded}",
                    }
                )
            else:
                checks.append(f"hip: {expanded}")

        lopoutput = _eval_string(comp, "lopoutput")
        if not lopoutput:
            errors.append(
                {
                    "code": "InvalidExportPath",
                    "message": "lopoutput is empty or cannot be evaluated.",
                }
            )
        else:
            checks.append(f"lopoutput: {lopoutput}")

        if not _node_can_export(comp):
            errors.append(
                {
                    "code": "ExportTriggerMissing",
                    "message": (
                        "No export trigger found "
                        "(saveToDisk/execute/render/renderbutton)."
                    ),
                }
            )

    hook_specs = _collect_hook_specs(node)
    for spec in hook_specs:
        try:
            _resolve_hook(spec)
        except Exception as exc:
            warnings.append({"code": "HookNotResolvable", "message": f"{spec}: {exc}"})

    if _eval_bool(node, "enable_gallery_sync", True):
        db_path = _eval_string(node, "gallery_db_override")
        if not db_path:
            db_path = (hou.getenv("HOUDINI_ASSETGALLERY_DATA_SOURCE") or "").strip()
        if not db_path:
            db_path = (hou.getenv("HOUDINI_ASSETGALLERY_DB_FILE") or "").strip()

        if not db_path:
            warnings.append(
                {
                    "code": "GalleryDBMissing",
                    "message": (
                        "No gallery DB configured "
                        "(override or HOUDINI_ASSETGALLERY_DATA_SOURCE)."
                    ),
                }
            )
        else:
            checks.append(f"gallery_db: {db_path}")

    result = {
        "status": "failed" if errors else "success",
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }
    _write_status(node, title="Preflight", payload=result)
    _apply_node_color(node, result)
    return result


def publish(node: hou.Node) -> dict[str, Any]:
    """Publish using the shared pipe.h.publish service."""
    _repair_broken_output_paths(node)

    options = _collect_publish_options(node)
    result = publish_component(node.path(), options)
    _write_status(node, title="Publish", payload=result)
    _apply_node_color(node, result)
    _show_ui_message(result, title="SKD Publish")
    return result


def _collect_publish_options(node: hou.Node) -> PublishOptions:
    asset_id = _eval_int(node, "asset_id_override")
    return PublishOptions(
        asset_root=_eval_path(node, "asset_root_override"),
        asset_name=_empty_to_none(_eval_string(node, "asset_name_override")),
        asset_path=_empty_to_none(_eval_string(node, "asset_path_override")),
        asset_id=asset_id,
        variant=_empty_to_none(_eval_string(node, "variant_override")),
        save_hip_before_publish=_eval_bool(node, "save_hip_before_publish", True),
        backup_dir=_eval_path(node, "backup_dir_override"),
        manifest_path=_eval_path(node, "manifest_path_override"),
        publish_note=_empty_to_none(_eval_string(node, "publish_note")),
        tool_version=_empty_to_none(_eval_string(node, "tool_version")),
        export_component=_eval_bool(node, "export_component", True),
        collect_thumbnail=_eval_bool(node, "collect_thumbnail", True),
        generate_thumbnail_if_missing=_eval_bool(
            node, "generate_thumbnail_if_missing", True
        ),
        update_gallery=_eval_bool(node, "enable_gallery_sync", True),
        gallery_db_path=_eval_path(node, "gallery_db_override"),
        gallery_label=_empty_to_none(_eval_string(node, "gallery_label_override")),
        prune_existing_items=_eval_bool(node, "prune_existing_items", True),
        fail_on_gallery_error=_eval_bool(node, "fail_on_gallery_error", False),
        hooks=tuple(_collect_hook_specs(node)),
        fail_on_hook_error=_eval_bool(node, "fail_on_hook_error", False),
    )


def _collect_hook_specs(node: hou.Node) -> list[str]:
    hooks: list[str] = []
    if _eval_bool(node, "hook_turnaround", False):
        hooks.append(TURNAROUND_HOOK)
    if _eval_bool(node, "hook_turnaround_shotgrid", False):
        hooks.append(TURNAROUND_SG_HOOK)

    raw_extra = _eval_string(node, "extra_hooks")
    for part in raw_extra.replace(",", "\n").splitlines():
        spec = part.strip()
        if spec:
            hooks.append(spec)
    return hooks


def _resolve_hook(spec: str) -> Callable[..., Any]:
    value = spec.strip()
    if ":" in value:
        module_name, attr = value.split(":", 1)
    else:
        module_name, _, attr = value.rpartition(".")
        if not module_name:
            module_name = value
            attr = "run"

    module = importlib.import_module(module_name)
    callback = getattr(module, attr)
    if not callable(callback):
        raise TypeError("hook is not callable")
    return callback


def _find_component_output(node: hou.Node) -> hou.LopNode | None:
    if isinstance(node, hou.LopNode) and node.type().name() == "componentoutput":
        return node

    for name in ("component_output", "componentoutput", "COMPONENT_OUT"):
        child = node.node(name)
        if isinstance(child, hou.LopNode) and child.type().name() == "componentoutput":
            return child

    for child in node.children():
        if isinstance(child, hou.LopNode) and child.type().name() == "componentoutput":
            return child

    try:
        descendants = node.allSubChildren()
    except Exception:
        descendants = ()
    for child in descendants:
        if (
            isinstance(child, hou.LopNode)
            and child.parent() == node
            and child.type().name() == "componentoutput"
        ):
            return child
    return None


def _default_asset_name() -> str:
    context_asset = ""
    try:
        context_asset = str(hou.contextOption("ASSET")).strip()
    except Exception:
        context_asset = ""
    if context_asset:
        return context_asset

    hip_dir = Path(hou.hscriptStringExpression("$HIP")).name.strip()
    if hip_dir:
        return hip_dir
    return "asset"


def _default_component_filename(*, asset_name: str | None = None) -> str:
    name_source = (asset_name or _default_asset_name()).strip()
    safe_name = _FILENAME_SAFE_RE.sub("_", name_source).strip("._")
    if not safe_name:
        safe_name = "asset"
    return f"{safe_name}.usd"


def _repair_broken_output_paths(node: hou.Node) -> None:
    """Repair legacy stock defaults that reference missing 'name' channels."""
    asset_name = _default_asset_name()
    filename_default = _default_component_filename(asset_name=asset_name)

    nodes: list[hou.Node] = [node]
    comp = _find_component_output(node)
    if comp is not None and comp.path() != node.path():
        nodes.append(comp)

    for current in nodes:
        _repair_filename_parm(current, filename_default)
        _repair_lopoutput_parm(current)


def _repair_filename_parm(node: hou.Node, default_value: str) -> None:
    parm = node.parm("filename")
    if parm is None:
        return

    text = _parm_unexpanded_string(parm).strip()
    if not text or any(token in text for token in _BROKEN_NAME_TOKENS):
        parm.set(default_value)


def _repair_lopoutput_parm(node: hou.Node) -> None:
    parm = node.parm("lopoutput")
    if parm is None:
        return

    text = _parm_unexpanded_string(parm).strip()
    if not text or any(token in text for token in _BROKEN_NAME_TOKENS):
        parm.set(_DEFAULT_LOPOUTPUT)
        return
    if any(token in text for token in _BROKEN_LOPOUTPUT_TOKENS):
        parm.set(_DEFAULT_LOPOUTPUT)


def _parm_unexpanded_string(parm: hou.Parm) -> str:
    try:
        return parm.unexpandedString()
    except Exception:
        try:
            return parm.evalAsString()
        except Exception:
            return ""


def _node_can_export(node: hou.Node) -> bool:
    if hasattr(node, "saveToDisk") and callable(node.saveToDisk):
        return True
    return any(
        node.parm(name) is not None for name in ("execute", "render", "renderbutton")
    )


def _eval_string(node: hou.Node, parm_name: str) -> str:
    parm = node.parm(parm_name)
    if parm is None:
        return ""
    try:
        return parm.evalAsString().strip()
    except Exception:
        return ""


def _eval_bool(node: hou.Node, parm_name: str, default: bool) -> bool:
    parm = node.parm(parm_name)
    if parm is None:
        return default
    try:
        return bool(parm.evalAsInt())
    except Exception:
        return default


def _eval_int(node: hou.Node, parm_name: str) -> int | None:
    text = _eval_string(node, parm_name)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _eval_path(node: hou.Node, parm_name: str) -> Path | None:
    text = _eval_string(node, parm_name)
    if not text:
        return None
    return Path(hou.expandString(text)).expanduser().resolve()


def _set_if_exists(node: hou.Node, parm_name: str, value: Any) -> None:
    parm = node.parm(parm_name)
    if parm is not None:
        parm.set(value)


def _empty_to_none(value: str) -> str | None:
    text = value.strip()
    return text or None


def _write_status(node: hou.Node, *, title: str, payload: dict[str, Any]) -> None:
    summary = _format_summary(title, payload)
    _set_if_exists(node, STATUS_SUMMARY_PARM, summary)
    _set_if_exists(
        node, STATUS_JSON_PARM, json.dumps(payload, indent=2, sort_keys=True)
    )
    node.setComment(summary)
    node.setGenericFlag(hou.nodeFlag.DisplayComment, True)


def _format_summary(title: str, payload: dict[str, Any]) -> str:
    warnings = payload.get("warnings", [])
    errors = payload.get("errors", [])
    status = str(payload.get("status", "unknown")).upper()
    lines = [
        f"{title}: {status}",
        f"Warnings: {len(warnings)}",
        f"Errors: {len(errors)}",
    ]

    warn_code = _first_message_code(warnings)
    err_code = _first_message_code(errors)
    if warn_code:
        lines.append(f"First Warning: {warn_code}")
    if err_code:
        lines.append(f"First Error: {err_code}")
    return "\n".join(lines)


def _first_message_code(messages: Any) -> str:
    if not isinstance(messages, list) or not messages:
        return ""
    first = messages[0]
    if isinstance(first, dict):
        code = str(first.get("code", "")).strip()
        if code:
            return code
    return ""


def _apply_node_color(node: hou.Node, payload: dict[str, Any]) -> None:
    has_errors = bool(payload.get("errors"))
    has_warnings = bool(payload.get("warnings"))
    if has_errors:
        node.setColor(hou.Color((0.60, 0.25, 0.25)))
    elif has_warnings:
        node.setColor(hou.Color((0.70, 0.60, 0.20)))
    else:
        node.setColor(hou.Color((0.25, 0.55, 0.32)))


def _show_ui_message(payload: dict[str, Any], *, title: str) -> None:
    if not getattr(hou, "isUIAvailable", lambda: False)():
        return

    warnings = payload.get("warnings", [])
    errors = payload.get("errors", [])

    severity = hou.severityType.Message
    if errors:
        severity = hou.severityType.Error
    elif warnings:
        severity = hou.severityType.Warning

    hou.ui.displayMessage(
        f"Status: {payload.get('status')}\nWarnings: {len(warnings)}\nErrors: {len(errors)}",
        severity=severity,
        title=title,
    )


__all__ = ["on_created", "preflight", "publish"]
