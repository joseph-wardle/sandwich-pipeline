"""Run hython to rebuild an asset's USD after a texture publish, and read its result."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from env import Executables
from pipe.dcc.houdini.launch import HoudiniLauncher
from Qt import QtCore

from pipe.core.asset import paths_for_asset
from pipe.core.shotgrid import Asset
from pipe.dcc.substance_painter.util.docs import LOG_HINT

log = logging.getLogger(__name__)

# Markers that bracket the JSON payload in hython stdout
_RESULT_START_MARKER = "--BUILD-RESULT--"
_RESULT_END_MARKER = "--END-BUILD-RESULT--"

_CANCEL_POLL_MS = 200


class HoudiniPublishError(RuntimeError):
    """Raised when the headless Houdini publish step fails."""


class HoudiniPublishCancelled(HoudiniPublishError):
    """Raised when the artist cancels before Houdini finishes."""


@dataclass(frozen=True)
class _ProcessResult:
    exit_code: int
    stdout: str
    stderr: str


def run_asset_builder(
    asset: Asset, *, geo_variant: str, is_cancelled: Callable[[], bool]
) -> dict[str, Any]:
    """Run the Houdini asset builder for the given asset and geometry variant.

    Blocks until hython exits, but keeps the Qt event loop running.
    Returns the structured result dict from hython on success.
    Raises HoudiniPublishError with a descriptive message on failure, and
    HoudiniPublishCancelled if *is_cancelled* turns true before hython exits.
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
    result = _run_process(command, env, is_cancelled)

    payload = _parse_result(result.stdout)
    if (
        payload is not None
        and result.exit_code == 0
        and payload.get("status") == "success"
    ):
        for warning in payload.get("warnings", []):
            log.warning(f"Houdini asset builder warning: {warning}")
        return payload

    log.error(f"Houdini asset builder stdout:\n{result.stdout}")
    log.error(f"Houdini asset builder stderr:\n{result.stderr}")
    raise HoudiniPublishError(_failure_reason(payload, result.exit_code))


def summarize_result(payload: dict[str, Any]) -> str:
    """Say what a successful Houdini build did, as a status line for the artist."""
    summary = payload.get("summary")
    if isinstance(summary, dict) and summary.get("builder_created"):
        line = "Houdini asset builder created and asset exported."
    else:
        # The build runs with --respect-existing, so an existing builder's
        # material graph is exported exactly as the artist left it.
        line = (
            "Houdini asset re-exported. New material variants still need adding "
            "in the asset builder."
        )

    children = payload.get("children", [])
    if isinstance(children, list) and children:
        line += f" {len(children)} piece(s) rebuilt first."

    # The builder copies its publish step's warnings into this top-level list.
    warnings = payload.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        line += f" Houdini reported {len(warnings)} warning(s). {LOG_HINT}"
    return line


def _run_process(
    command: list[str], env: dict[str, str], is_cancelled: Callable[[], bool]
) -> _ProcessResult:
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

    def stop_if_cancelled() -> None:
        if is_cancelled():
            loop.quit()

    process.stateChanged.connect(on_state_changed)
    cancel_poll = QtCore.QTimer()
    cancel_poll.timeout.connect(stop_if_cancelled)
    cancel_poll.start(_CANCEL_POLL_MS)
    process.start(command[0], command[1:])
    if process.state() != QtCore.QProcess.NotRunning:
        loop.exec_()
    cancel_poll.stop()

    if process.error() == QtCore.QProcess.FailedToStart:
        log.error(f"Could not start {command[0]}: {process.errorString()}")
        raise HoudiniPublishError(
            "Failed to execute hython; verify Houdini is installed."
        )
    if process.state() != QtCore.QProcess.NotRunning:
        # The artist cancelled, or the event loop was told to exit, e.g. because
        # Painter is quitting.
        # Only hython is killed; a child it started, such as husk, runs on.
        process.kill()
        process.waitForFinished()
        if is_cancelled():
            raise HoudiniPublishCancelled("Cancelled before Houdini finished.")
        raise HoudiniPublishError("Interrupted before Houdini finished.")

    stdout = process.readAllStandardOutput().data().decode("utf-8", "replace")
    stderr = process.readAllStandardError().data().decode("utf-8", "replace")
    if process.exitStatus() == QtCore.QProcess.CrashExit:
        log.error(f"Houdini asset builder stdout:\n{stdout}")
        log.error(f"Houdini asset builder stderr:\n{stderr}")
        raise HoudiniPublishError(f"Houdini crashed. {LOG_HINT}")
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


def _failure_reason(payload: dict[str, Any] | None, exit_code: int) -> str:
    """Why the build failed, worded to follow 'Houdini publish failed: '."""
    if payload is None:
        return (
            f"Houdini exited with code {exit_code} without reporting a result. "
            f"{LOG_HINT}"
        )
    # The builder copies its publish step's errors into this top-level list.
    errors = payload.get("errors", [])
    if isinstance(errors, list):
        messages = [
            str(entry.get("message", ""))
            for entry in errors
            if isinstance(entry, dict) and entry.get("message")
        ]
        if messages:
            return "; ".join(messages)
    status = payload.get("status", "unknown")
    return (
        f"Houdini reported no error, but ended with status {status!r} and "
        f"exit code {exit_code}. {LOG_HINT}"
    )
