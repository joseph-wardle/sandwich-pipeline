"""Build Houdini component packages for the Bobo asset pipeline."""

from __future__ import annotations

import argparse
import datetime
import enum
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import TypedDict

import hou

from pipe.h import nodelayouts

log = logging.getLogger(__name__)


class BuildError(TypedDict):
    """A structured error for machine-readable client output."""

    code: str
    message: str


class BuildResult(TypedDict):
    """Structured description of the outcome of a component package build."""

    status: str
    mode: str
    hip_path: str
    usd_path: str
    export_dir: str
    export_performed: bool
    variant: str | None
    changed_usd_reference: bool
    warnings: list[str]
    errors: list[BuildError]


class BuildMode(enum.Enum):
    """Whether to create a new HIP file or update an existing one."""

    CREATE = "create"
    UPDATE = "update"


def _configure_logging(level: str) -> None:
    """Normalize CLI logging so build output stays predictable."""
    level_name = level.upper()
    numeric_level = getattr(logging, level_name, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
        log.warning("Unknown log level %s, defaulting to INFO", level)
    logging.basicConfig(
        level=numeric_level, format="[assetbuilder] %(levelname)s: %(message)s"
    )


def _set_parm(node: hou.Node, name: str, value) -> None:
    """Set a Houdini parameter and warn if templates drift from expectations."""
    parm = node.parm(name)
    if parm is None:
        log.warning("Parameter %s missing on %s", name, node.path())
        return
    parm.set(value)


def _find_node(name: str, node_type: type[hou.Node] | None = None) -> hou.Node | None:
    """Find a node by name, optionally validating its type."""
    node = hou.node(f"/stage/{name}")
    if node and node_type and not isinstance(node, node_type):
        log.warning(
            "Found node %s but it has wrong type (expected %s, got %s)",
            name,
            node_type.__name__,
            node.type().name(),
        )
        return None
    return node


def _get_node_hash(node: hou.Node) -> str | None:
    """Retrieve a hash from a node's user data."""
    return node.userData("bobo_pipeline_hash")


def _set_node_hash(node: hou.Node, file_path: Path) -> None:
    """Store a hash of the file path on the node's user data."""
    node.setUserData("bobo_pipeline_hash", str(hash(file_path)))


def _create_hip_file(
    *,
    result: BuildResult,
    hip_path: Path,
    usd_path: Path,
    component_name: str,
    root_prim: str | None = None,
) -> None:
    """Create a new Houdini scene with a standard component network."""
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.hipFile.setName(str(hip_path))

    stage = hou.node("/stage")
    if not stage:
        raise RuntimeError("Could not find /stage in Houdini session")

    log.info("Building Bobo component network")
    geo_node = nodelayouts.bobo_componentgeometry({}, parent=stage)
    cmat_node = nodelayouts.lnd_componentmaterial({}, parent=stage)
    lib_node = nodelayouts.create_skd_matlib(stage, "matlib")
    config_node = stage.createNode("sdm223::lnd_componentconfig", "config")
    lookdev_node = nodelayouts.create_skd_lookdev(stage, "lookdev")
    env_node = stage.createNode("fetch", "env")
    out_node = stage.createNode("componentoutput", "COMPONENT_OUT")
    out_node.setColor(hou.Color((0.616, 0.871, 0.769)))

    out_node.setInput(0, config_node)
    out_node.setInput(1, env_node)
    config_node.setInput(0, cmat_node)
    cmat_node.setInput(0, geo_node)
    cmat_node.setInput(1, lib_node)
    lookdev_node.setInput(0, out_node)

    _set_parm(env_node, "loppath", f"../{lookdev_node.name()}/OUT_ENV")

    for node in stage.children():
        node.moveToGoodPosition()

    # Configure the import node inside the component geometry HDA.
    importer = geo_node.node("sopnet/geo/import_usd")
    if not importer:
        result["errors"].append(
            {
                "code": "NetworkMissingError",
                "message": "Component Geometry SOP is missing 'import_usd' node",
            }
        )
        return

    _set_parm(importer, "filepath1", usd_path.as_posix())
    _set_node_hash(importer, usd_path)
    result["changed_usd_reference"] = True

    # Configure the final output node.
    root_name = root_prim or component_name
    _set_parm(out_node, "lopoutput", '$HIP/export/`chs("filename")`')
    _set_parm(out_node, "rootprim", f"/{root_name}")
    _set_parm(out_node, "localize", False)
    _set_parm(out_node, "thumbnailmode", 2)
    _set_parm(out_node, "renderer", "RenderMan RIS")
    _set_parm(out_node, "thumbnailscenesource", 1)
    _set_parm(out_node, "thumbnailinputcamera", "/lookdev/cam")

    out_node.setCurrent(True, clear_all_selected=True)
    if hasattr(out_node, "setDisplayFlag"):
        out_node.setDisplayFlag(True)


def _update_hip_file(*, result: BuildResult, hip_path: Path, usd_path: Path) -> None:
    """Load an existing HIP and update the USD reference if it has changed."""
    try:
        hou.hipFile.load(str(hip_path), suppress_save_prompt=True)
    except hou.LoadWarning as exc:
        result["warnings"].append(f"Houdini load warning: {exc}")

    geo_node = _find_node("main", hou.LopNode)
    if not geo_node:
        result["errors"].append(
            {
                "code": "NetworkMissingError",
                "message": "Expected to find a 'main' LOP node in /stage",
            }
        )
        return

    importer = geo_node.node("sopnet/geo/import_usd")
    if not importer:
        result["errors"].append(
            {
                "code": "NetworkMissingError",
                "message": "Component Geometry SOP is missing 'import_usd' node",
            }
        )
        return

    # Only update the path if the new USD is different from the tracked one.
    current_hash = _get_node_hash(importer)
    new_hash = str(hash(usd_path))

    if current_hash != new_hash:
        log.info("Updating USD reference path")
        _set_parm(importer, "filepath1", usd_path.as_posix())
        _set_node_hash(importer, usd_path)
        result["changed_usd_reference"] = True
    else:
        log.info("USD reference is already up-to-date")


def _export_component(*, result: BuildResult, export_dir: Path) -> None:
    """Trigger the component output node to save the package to disk."""
    if result["errors"]:
        log.warning("Skipping export due to previous errors")
        return

    out_node = _find_node("COMPONENT_OUT")
    if not out_node:
        result["errors"].append(
            {
                "code": "NetworkMissingError",
                "message": "Cannot find 'COMPONENT_OUT' node to trigger export",
            }
        )
        return

    previous_errors = tuple(out_node.errors())
    executed = False

    try:
        # Modern Houdini versions use a simple `saveToDisk` method.
        if hasattr(out_node, "saveToDisk") and callable(out_node.saveToDisk):
            if not out_node.saveToDisk():
                raise RuntimeError("Component Output node failed to save to disk")
            executed = True
        # Fallback for older versions or different node types.
        else:
            for button in ("execute", "render", "renderbutton"):
                parm = out_node.parm(button)
                if parm:
                    parm.pressButton()
                    executed = True
                    break
    except (RuntimeError, hou.OperationFailed) as exc:
        result["errors"].append({"code": "ExportExecutionError", "message": str(exc)})
        return

    if not executed:
        result["errors"].append(
            {
                "code": "NodePatchError",
                "message": "No method found to trigger component output node",
            }
        )
        return

    new_errors = [err for err in out_node.errors() if err not in previous_errors]
    if new_errors:
        joined = "; ".join(new_errors)
        result["errors"].append(
            {
                "code": "ExportExecutionError",
                "message": f"Component output reported errors: {joined}",
            }
        )
        return

    result["export_performed"] = True
    log.info("Component export successful")


def build_component_package(  # noqa: C901
    *,
    hip_path: Path,
    usd_path: Path,
    export_dir: Path,
    component_name: str,
    asset_name: str | None = None,
    root_prim: str | None = None,
    variant: str | None = None,
    clean_export: bool = False,
    export: bool = True,
) -> BuildResult:
    """Orchestrate the build or update of a Houdini component package.

    This layer is responsible for discovery and decision-making, but does not
    use the ``hou`` module directly. It prepares a plan and then calls the
    appropriate function to execute it in a Houdini session.

    Args:
        hip_path: Destination .hipnc path.
        usd_path: Source USD file exported from Maya.
        export_dir: Directory where component USD layers will be written.
        component_name: Component identifier used for filenames and root prim.
        asset_name: Name stored on the ASSET context option.
        root_prim: Optional override for the component root prim name.
        variant: Geometry variant name.
        clean_export: If True, remove the export directory before writing.
        export: If False, skip the export step (dry-run).

    Returns:
        A dictionary summarizing the outcome of the build.
    """
    result: BuildResult = {
        "status": "success",
        "mode": "",  # Set dynamically
        "hip_path": str(hip_path.resolve()),
        "usd_path": str(usd_path.resolve()),
        "export_dir": str(export_dir.resolve()),
        "export_performed": False,
        "variant": variant,
        "changed_usd_reference": False,
        "warnings": [],
        "errors": [],
    }

    try:
        if not usd_path.exists():
            raise FileNotFoundError(f"USD file not found: {usd_path}")

        build_mode = BuildMode.UPDATE if hip_path.exists() else BuildMode.CREATE
        result["mode"] = build_mode.value

        if clean_export and export_dir.exists():
            log.info("Cleaning existing export directory: %s", export_dir)
            backup_dir = export_dir.parent / "export_backups"
            if backup_dir.exists() or not any(export_dir.iterdir()):
                shutil.rmtree(export_dir)
            else:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = backup_dir / f"{timestamp}"
                log.info("Backing up existing export to %s", backup_path)
                shutil.move(str(export_dir), str(backup_path))

        export_dir.mkdir(parents=True, exist_ok=True)
        hip_path.parent.mkdir(parents=True, exist_ok=True)

        hou.setContextOption("ASSET", asset_name or component_name)

        if build_mode == BuildMode.CREATE:
            log.info("Creating new Houdini file at %s", hip_path)
            _create_hip_file(
                result=result,
                hip_path=hip_path,
                usd_path=usd_path,
                component_name=component_name,
                root_prim=root_prim,
            )
        else:
            log.info("Updating existing Houdini file at %s", hip_path)
            _update_hip_file(result=result, hip_path=hip_path, usd_path=usd_path)

        if export:
            log.info("Saving component package to %s", export_dir)
            _export_component(result=result, export_dir=export_dir)

        if not result["errors"]:
            hou.hipFile.save(file_name=str(hip_path))
            log.info("Component package build complete")

    except Exception as exc:
        log.exception("Failed to build component package: %s", exc)
        result["status"] = "failed"
        result["errors"].append({"code": "UnhandledException", "message": str(exc)})

    return result


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for command-line execution."""
    parser = argparse.ArgumentParser(
        description="Build Houdini component package from Maya export"
    )
    parser.add_argument("--hip-path", required=True, help="Destination .hipnc path")
    parser.add_argument(
        "--usd-path", required=True, help="Source USD file exported from Maya"
    )
    parser.add_argument(
        "--export-dir",
        required=True,
        help="Directory where component USD layers will be written",
    )
    parser.add_argument(
        "--component-name",
        required=True,
        help="Component identifier used for filenames and root prim",
    )
    parser.add_argument(
        "--asset-name",
        help="Name stored on the ASSET context option for downstream tools",
    )
    parser.add_argument(
        "--root-prim", help="Optional override for the component root prim name"
    )
    parser.add_argument("--variant", help="Geometry variant name (for logging only)")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    parser.add_argument(
        "--clean-export",
        action="store_true",
        help="Remove the export directory before writing new files",
    )

    args = parser.parse_args(argv or sys.argv[1:])
    _configure_logging(args.log_level)

    if args.variant:
        log.info("Processing variant: %s", args.variant)

    result = build_component_package(
        hip_path=Path(args.hip_path),
        usd_path=Path(args.usd_path),
        export_dir=Path(args.export_dir),
        component_name=args.component_name,
        asset_name=args.asset_name,
        root_prim=args.root_prim,
        variant=args.variant,
        clean_export=args.clean_export,
        export=True,
    )

    # Always print the JSON result to stdout for the client to parse.
    try:
        json_result = json.dumps(result, indent=2)
        sys.stdout.write("\n--BUILD-RESULT--\n")
        sys.stdout.write(json_result)
        sys.stdout.write("\n--END-BUILD-RESULT--\n")
        sys.stdout.flush()
    except TypeError:
        log.error("Failed to serialize build result to JSON: %s", result)
        return 1

    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
