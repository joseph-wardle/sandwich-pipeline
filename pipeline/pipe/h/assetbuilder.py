"""Headless Houdini asset-builder entrypoint for unified component publish.

This module is the single integration point for DCC tools (Maya/Substance)
that need to trigger Houdini publishes without opening the Houdini UI.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, TypedDict

import hou

from pipe.asset.paths import ASSET_BUILDER_FILENAME

from . import nodelayouts
from .publish import PublishOptions, publish_component

log = logging.getLogger(__name__)

RESULT_START_MARKER = "--BUILD-RESULT--"
RESULT_END_MARKER = "--END-BUILD-RESULT--"

TURNAROUND_HOOK = "pipe.h.publish_hooks.turnaround:run"


class ResultMessage(TypedDict):
    code: str
    message: str


class BuilderSummary(TypedDict):
    hip_path: str
    builder_node_path: str
    hip_created: bool
    builder_created: bool
    variant_graph_regenerated: bool
    respected_existing: bool


class HeadlessPublishResult(TypedDict):
    status: str
    asset_root: str
    asset_name: str
    variant: str
    ensure_builder: bool
    publish_requested: bool
    summary: BuilderSummary | None
    publish: dict[str, Any] | None
    warnings: list[ResultMessage]
    errors: list[ResultMessage]


def run_headless_publish(
    *,
    asset_root: Path,
    asset_name: str | None = None,
    asset_path: str | None = None,
    asset_id: int | None = None,
    variant: str | None = None,
    ensure_builder: bool = False,
    publish: bool = False,
    respect_existing: bool = True,
    regen_managed_variants: bool = False,
    run_hooks: bool = False,
    turnaround: bool = False,
    fail_on_hook_error: bool = False,
) -> HeadlessPublishResult:
    """Run headless SKD builder ensure/publish operations.

    Behavior:
    - Uses canonical asset builder path: `<asset_root>/asset_builder.hipnc`
    - Creates builder only when missing
    - Respects existing artist graph by default
    - Regenerates managed variant graph only when requested
    - Delegates publish execution to `pipe.h.publish.publish_component`
    """

    normalized_variant = (variant or "").strip() or "main"
    result: HeadlessPublishResult = {
        "status": "failed",
        "asset_root": str(asset_root.expanduser().resolve()),
        "asset_name": "",
        "variant": normalized_variant,
        "ensure_builder": bool(ensure_builder),
        "publish_requested": bool(publish),
        "summary": None,
        "publish": None,
        "warnings": [],
        "errors": [],
    }

    ensure_requested = ensure_builder or publish
    if not ensure_requested and not publish:
        _error(
            result,
            "NoActionRequested",
            "No action requested. Use --ensure-builder and/or --publish.",
        )
        return _finalize(result)

    root = asset_root.expanduser().resolve()
    hip_path = root / ASSET_BUILDER_FILENAME
    resolved_asset_name = (asset_name or root.name).strip() or "asset"
    result["asset_name"] = resolved_asset_name
    result["ensure_builder"] = ensure_requested
    result["publish_requested"] = bool(publish)

    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _error(
            result,
            "AssetRootCreateFailed",
            f"Failed to create asset root {root}: {exc}",
        )
        return _finalize(result)

    hip_created = _load_or_initialize_hip(hip_path=hip_path, result=result)
    if hip_created is None:
        return _finalize(result)

    _set_asset_context(resolved_asset_name, result=result)

    stage = _resolve_stage(result=result)
    if stage is None:
        return _finalize(result)

    builder = None
    builder_created = False
    variant_graph_regenerated = False

    if ensure_requested:
        preexisting_outputs = _component_output_paths(stage)
        try:
            builder = nodelayouts.ensure_managed_skd_component_builder(stage)
        except Exception as exc:
            _error(
                result,
                "EnsureBuilderFailed",
                f"Failed to ensure managed SKD component builder: {exc}",
            )
            return _finalize(result)

        if builder is None:
            _error(
                result,
                "BuilderResolveFailed",
                "ensure_managed_skd_component_builder returned no node.",
            )
            return _finalize(result)

        builder_created = builder.path() not in preexisting_outputs
        should_regen = regen_managed_variants or (
            (not respect_existing) and (not builder_created)
        )
        if should_regen:
            try:
                warnings = nodelayouts.rebuild_managed_skd_variant_graph(builder)
                variant_graph_regenerated = True
            except Exception as exc:
                _error(
                    result,
                    "VariantGraphRebuildFailed",
                    f"Failed to rebuild managed variant graph: {exc}",
                )
                return _finalize(result)

            for warning in warnings:
                _warn(result, "VariantGraphWarning", warning)

    if ensure_requested and not publish:
        if not _save_hip(hip_path=hip_path, result=result):
            return _finalize(result)

    if publish:
        if builder is None:
            _error(
                result,
                "BuilderMissing",
                "Cannot publish because no SKD Component Output node is available.",
            )
            return _finalize(result)

        hooks = _collect_hook_specs(run_hooks=run_hooks, turnaround=turnaround)
        if run_hooks and not hooks:
            _warn(
                result,
                "HooksEnabledNoSpecs",
                "--run-hooks enabled but no hook toggles were requested.",
            )

        options = PublishOptions(
            asset_root=root,
            asset_name=resolved_asset_name,
            asset_path=asset_path,
            asset_id=asset_id,
            variant=normalized_variant,
            geo_variant=normalized_variant,
            hooks=tuple(hooks),
            fail_on_hook_error=fail_on_hook_error,
        )

        try:
            publish_result = publish_component(builder.path(), options)
        except Exception as exc:
            _error(
                result,
                "PublishServiceFailed",
                f"Unhandled exception in publish_component: {exc}",
            )
            return _finalize(result)

        result["publish"] = publish_result
        for warning in publish_result.get("warnings", []):
            _warn(
                result,
                f"Publish::{warning.get('code', 'Warning')}",
                str(warning.get("message", "")),
            )
        for error in publish_result.get("errors", []):
            _error(
                result,
                f"Publish::{error.get('code', 'Error')}",
                str(error.get("message", "")),
            )

    result["summary"] = {
        "hip_path": str(hip_path),
        "builder_node_path": builder.path() if builder is not None else "",
        "hip_created": hip_created,
        "builder_created": builder_created,
        "variant_graph_regenerated": variant_graph_regenerated,
        "respected_existing": bool(respect_existing),
    }
    return _finalize(result)


def _collect_hook_specs(*, run_hooks: bool, turnaround: bool) -> list[str]:
    if not run_hooks and not turnaround:
        return []
    hooks: list[str] = []
    if turnaround:
        hooks.append(TURNAROUND_HOOK)
    return hooks


def _load_or_initialize_hip(
    *, hip_path: Path, result: HeadlessPublishResult
) -> bool | None:
    try:
        hip_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _error(
            result,
            "HipDirectoryCreateFailed",
            f"Failed to create HIP directory {hip_path.parent}: {exc}",
        )
        return None

    if hip_path.exists():
        try:
            hou.hipFile.load(str(hip_path), suppress_save_prompt=True)
        except hou.LoadWarning as exc:
            _warn(result, "HipLoadWarning", str(exc))
        except Exception as exc:
            _error(result, "HipLoadFailed", f"Failed to load HIP {hip_path}: {exc}")
            return None
        return False

    try:
        hou.hipFile.clear(suppress_save_prompt=True)
        hou.hipFile.setName(str(hip_path))
    except Exception as exc:
        _error(result, "HipCreateFailed", f"Failed to initialize HIP {hip_path}: {exc}")
        return None
    return True


def _save_hip(*, hip_path: Path, result: HeadlessPublishResult) -> bool:
    try:
        hou.hipFile.save(file_name=str(hip_path))
    except Exception as exc:
        _error(result, "HipSaveFailed", f"Failed to save HIP {hip_path}: {exc}")
        return False
    return True


def _resolve_stage(*, result: HeadlessPublishResult) -> hou.Node | None:
    stage = hou.node("/stage")
    if stage is not None:
        return stage

    root = hou.node("/")
    if root is None:
        _error(result, "RootNodeMissing", "Houdini root node '/' is unavailable.")
        return None

    try:
        return root.createNode("lopnet", "stage")
    except Exception as exc:
        _error(
            result, "StageCreateFailed", f"Failed to create /stage LOP network: {exc}"
        )
        return None


def _component_output_paths(stage: hou.Node) -> set[str]:
    paths: set[str] = set()
    for child in stage.children():
        node_type = child.type().name().lower()
        if node_type == "componentoutput" or "skd_component_output" in node_type:
            paths.add(child.path())
    return paths


def _set_asset_context(asset_name: str, *, result: HeadlessPublishResult) -> None:
    try:
        hou.setContextOption("ASSET", asset_name)
    except Exception as exc:
        _warn(
            result,
            "AssetContextOptionFailed",
            f"Failed to set ASSET context option to '{asset_name}': {exc}",
        )


def _warn(result: HeadlessPublishResult, code: str, message: str) -> None:
    result["warnings"].append({"code": code, "message": message})
    log.warning("%s: %s", code, message)


def _error(result: HeadlessPublishResult, code: str, message: str) -> None:
    result["errors"].append({"code": code, "message": message})
    log.error("%s: %s", code, message)


def _finalize(result: HeadlessPublishResult) -> HeadlessPublishResult:
    result["status"] = "failed" if result["errors"] else "success"
    return result


def _configure_logging(level: str) -> None:
    level_name = level.upper().strip()
    numeric_level = getattr(logging, level_name, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
    logging.basicConfig(
        level=numeric_level, format="[assetbuilder] %(levelname)s: %(message)s"
    )


def _emit_result(result: HeadlessPublishResult) -> None:
    sys.stdout.write("\n")
    sys.stdout.write(RESULT_START_MARKER)
    sys.stdout.write("\n")
    sys.stdout.write(json.dumps(result, indent=2))
    sys.stdout.write("\n")
    sys.stdout.write(RESULT_END_MARKER)
    sys.stdout.write("\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Headless SKD Houdini asset-builder and publish runner."
    )
    parser.add_argument(
        "--asset-root", required=True, help="Absolute/relative asset root path."
    )
    parser.add_argument(
        "--asset-name", help="Asset name override for ASSET context option."
    )
    parser.add_argument(
        "--asset-path", help="ShotGrid asset path metadata for manifest entries."
    )
    parser.add_argument(
        "--asset-id", type=int, help="ShotGrid asset id metadata for manifest entries."
    )
    parser.add_argument(
        "--variant", default="main", help="Publish variant label (default: main)."
    )

    parser.add_argument(
        "--publish", action="store_true", help="Run publish after ensuring builder."
    )
    parser.add_argument(
        "--ensure-builder",
        action="store_true",
        help="Ensure asset_builder.hipnc and one managed SKD builder exist.",
    )
    parser.add_argument(
        "--respect-existing",
        dest="respect_existing",
        action="store_true",
        default=True,
        help="Respect existing artist graph (default behavior).",
    )
    parser.add_argument(
        "--no-respect-existing",
        dest="respect_existing",
        action="store_false",
        help="Regenerate managed variant graph for existing builders.",
    )
    parser.add_argument(
        "--regen-managed-variants",
        action="store_true",
        help="Explicitly rebuild managed variant graph before publish.",
    )
    parser.add_argument(
        "--run-hooks",
        action="store_true",
        help="Enable selected publish hooks.",
    )
    parser.add_argument(
        "--turnaround",
        action="store_true",
        help="Run turnaround publish hook.",
    )
    parser.add_argument(
        "--fail-on-hook-error",
        action="store_true",
        help="Fail publish when a hook fails.",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")

    args = parser.parse_args(argv or sys.argv[1:])
    _configure_logging(args.log_level)

    result = run_headless_publish(
        asset_root=Path(args.asset_root),
        asset_name=args.asset_name,
        asset_path=args.asset_path,
        asset_id=args.asset_id,
        variant=args.variant,
        ensure_builder=args.ensure_builder,
        publish=args.publish,
        respect_existing=args.respect_existing,
        regen_managed_variants=args.regen_managed_variants,
        run_hooks=args.run_hooks,
        turnaround=args.turnaround,
        fail_on_hook_error=args.fail_on_hook_error,
    )

    _emit_result(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "HeadlessPublishResult",
    "RESULT_END_MARKER",
    "RESULT_START_MARKER",
    "run_headless_publish",
]
