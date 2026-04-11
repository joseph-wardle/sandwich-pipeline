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
import subprocess
from pathlib import Path
from typing import Any

from env import Executables
from software.houdini.dcc import HoudiniDCC

from pipe.asset.paths import paths_for_asset
from pipe.struct.db import Asset

log = logging.getLogger(__name__)

# Markers that bracket the JSON payload in hython stdout
_RESULT_START_MARKER = "--BUILD-RESULT--"
_RESULT_END_MARKER = "--END-BUILD-RESULT--"


class HoudiniPublishError(RuntimeError):
    """Raised when the headless Houdini publish step fails."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_asset_builder(asset: Asset, *, geo_variant: str) -> dict[str, Any]:
    """Run the Houdini asset builder for the given asset and geometry variant.

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
        "pipe.h.assetbuilder",
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

    dcc = HoudiniDCC(is_python_shell=True)
    env = dcc._get_env_vars()
    env["PIPE_LOG_LEVEL"] = str(log.getEffectiveLevel())

    log.info(
        f"Running headless Houdini publish from Substance for {asset_name} "
        f"(geo={geo_variant})"
    )
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise HoudiniPublishError(
            "Failed to execute hython; verify Houdini is installed."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stdout = exc.stdout or ""
        payload = _parse_result(stdout)
        if payload is not None:
            raise HoudiniPublishError(_summarize_errors(payload)) from exc
        if stdout:
            log.error(f"Houdini asset builder stdout:\n{stdout}")
        if exc.stderr:
            log.error(f"Houdini asset builder stderr:\n{exc.stderr}")
        raise HoudiniPublishError(
            f"Houdini publish failed with exit code {exc.returncode}"
        ) from exc

    payload = _parse_result(completed.stdout or "")
    if payload is None:
        log.error(f"Houdini asset builder stdout:\n{completed.stdout or ''}")
        log.error(f"Houdini asset builder stderr:\n{completed.stderr or ''}")
        raise HoudiniPublishError(
            "Failed to parse structured output from Houdini publish."
        )

    if payload.get("status") != "success":
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
