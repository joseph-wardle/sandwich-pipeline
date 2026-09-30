"""Headless Houdini asset builder integration for Substance Painter.

After textures are exported and converted, the publish workflow runs hython
as a subprocess to rebuild the USD asset (materials, galleries, etc.).
This module encapsulates that subprocess call and its result parsing.

Public API
----------
- run_asset_builder(asset, geo_variant) -> structured result dict
- summarize_result(payload) -> human-readable summary string
- HoudiniPublishError — raised when the Houdini step fails
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from env import Executables
from pipe.dcc.houdini.launch import HoudiniLauncher
from Qt import QtCore

from pipe.core.asset import paths_for_asset
from pipe.core.shotgrid import Asset

log = logging.getLogger(__name__)

# Markers that bracket the JSON payload in hython stdout
_RESULT_START_MARKER = "--BUILD-RESULT--"
_RESULT_END_MARKER = "--END-BUILD-RESULT--"


class HoudiniPublishError(RuntimeError):
    """Raised when the headless Houdini publish step fails."""


@dataclass(frozen=True)
class _ProcessResult:
    exit_code: int
    stdout: str
    stderr: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_asset_builder(asset: Asset, *, geo_variant: str) -> dict[str, Any]:
    """Run the Houdini asset builder for the given asset and geometry variant.

    Blocks until hython exits, but keeps the Qt event loop running.
    Returns the structured result dict from hython on success.
    Raises HoudiniPublishError with a descriptive message on failure.
    """
    if not Executables.hython.exists():
        raise HoudiniPublishError(
            f"Houdini executable not found at {Executables.hython}"
        )

    asset_paths = paths_for_asset(asset)
    asset_name = asset.name or asset.display_name or asset_paths.root.name
    command = [
        str(Executables.hython),
        "-m",
        "pipe.dcc.houdini.publish.assetbuilder",
        "--asset-root",
        str(asset_paths.root),
        "--asset-name",
        asset_name,
        "--variant",
        geo_variant,
        "--ensure-builder",
        "--publish",
        "--respect-existing",
    ]

    if asset.asset_path:
        command.extend(["--asset-path", asset.asset_path])
    if asset.id is not None:
        command.extend(["--asset-id", str(asset.id)])

    dcc = HoudiniLauncher(is_python_shell=True)
    env = dcc._get_env_vars()
    env["PIPE_LOG_LEVEL"] = str(log.getEffectiveLevel())

    log.info(
        f"Running headless Houdini publish from Substance for {asset_name} "
        f"(geo={geo_variant})"
    )
    result = _run_process(command, env)

    payload = _parse_result(result.stdout)
    if payload is None:
        log.error(f"Houdini asset builder stdout:\n{result.stdout}")
        log.error(f"Houdini asset builder stderr:\n{result.stderr}")
        if result.exit_code != 0:
            raise HoudiniPublishError(
                f"Houdini publish failed with exit code {result.exit_code}"
            )
        raise HoudiniPublishError(
            "Failed to parse structured output from Houdini publish."
        )

    if result.exit_code != 0 or payload.get("status") != "success":
        raise HoudiniPublishError(_summarize_errors(payload))
    return payload


def summarize_result(payload: dict[str, Any]) -> str:
    """Turn a successful Houdini build result into a one-line summary."""
    status = str(payload.get("status", "unknown")).capitalize()
    parts = [f"Houdini publish: {status}"]

    summary = payload.get("summary")
    if isinstance(summary, dict):
        if summary.get("builder_created"):
            parts.append("builder created")
        else:
            parts.append("builder reused")

    publish_payload = payload.get("publish")
    if isinstance(publish_payload, dict):
        export = publish_payload.get("export")
        if isinstance(export, dict):
            export_path = str(export.get("export_path", "")).strip()
            if export_path:
                parts.append(f"exported {Path(export_path).name}")

        gallery = publish_payload.get("gallery")
        if isinstance(gallery, dict):
            gallery_status = str(gallery.get("status", "")).strip()
            if gallery_status:
                parts.append(f"gallery {gallery_status}")

        warnings = publish_payload.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            parts.append(f"{len(warnings)} publish warning(s)")

    warnings = payload.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        parts.append(f"{len(warnings)} warning(s)")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_process(command: list[str], env: dict[str, str]) -> _ProcessResult:
    """Run *command* to completion in a nested Qt event loop; capture its output."""
    environment = QtCore.QProcessEnvironment()
    for key, value in env.items():
        environment.insert(key, value)
    process = QtCore.QProcess()
    process.setProcessEnvironment(environment)
    # Anything reading stdin (e.g. a stray breakpoint) gets EOF instead of hanging.
    process.setStandardInputFile(QtCore.QProcess.nullDevice())

    loop = QtCore.QEventLoop()

    # Unlike finished, this also fires when the process fails to start.
    def on_state_changed(state: QtCore.QProcess.ProcessState) -> None:
        if state == QtCore.QProcess.NotRunning:
            loop.quit()

    process.stateChanged.connect(on_state_changed)
    process.start(command[0], command[1:])
    if process.state() != QtCore.QProcess.NotRunning:
        loop.exec_()

    if process.error() == QtCore.QProcess.FailedToStart:
        log.error(f"Could not start {command[0]}: {process.errorString()}")
        raise HoudiniPublishError(
            "Failed to execute hython; verify Houdini is installed."
        )
    if process.state() != QtCore.QProcess.NotRunning:
        # The event loop was told to exit, e.g. because Painter is quitting.
        # Only hython is killed; a child it started, such as husk, runs on.
        process.kill()
        process.waitForFinished()
        raise HoudiniPublishError("The Houdini publish was interrupted.")

    stdout = process.readAllStandardOutput().data().decode("utf-8", "replace")
    stderr = process.readAllStandardError().data().decode("utf-8", "replace")
    if process.exitStatus() == QtCore.QProcess.CrashExit:
        log.error(f"Houdini asset builder stdout:\n{stdout}")
        log.error(f"Houdini asset builder stderr:\n{stderr}")
        raise HoudiniPublishError("Houdini crashed during the publish.")
    return _ProcessResult(process.exitCode(), stdout, stderr)


def _parse_result(stdout: str) -> dict[str, Any] | None:
    """Extract the JSON payload bracketed by result markers in hython stdout."""
    start = stdout.find(_RESULT_START_MARKER)
    end = stdout.find(_RESULT_END_MARKER)
    if start == -1 or end == -1:
        return None
    json_text = stdout[start + len(_RESULT_START_MARKER) : end]
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _summarize_errors(payload: dict[str, Any]) -> str:
    """Extract error messages from a failed Houdini build result."""
    errors = payload.get("errors", [])
    if isinstance(errors, list):
        messages = [
            str(entry.get("message", ""))
            for entry in errors
            if isinstance(entry, dict) and entry.get("message")
        ]
        if messages:
            return "; ".join(messages)
    publish_payload = payload.get("publish")
    if isinstance(publish_payload, dict):
        publish_errors = publish_payload.get("errors", [])
        if isinstance(publish_errors, list):
            messages = [
                str(entry.get("message", ""))
                for entry in publish_errors
                if isinstance(entry, dict) and entry.get("message")
            ]
            if messages:
                return "; ".join(messages)
    return "Unknown Houdini publish error."
