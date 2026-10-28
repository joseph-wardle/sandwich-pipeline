"""What every Send shares: the checks before it, the environment its tasks run
in, spooling the job, and what it tells the artist."""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

import hou
import tractor.api.author as author

from env import Executables
from pipe.dcc.houdini import launch
from pipe.dcc.houdini.tractor import SendRefused

BUILD_FAILED = (
    "The job could not be built, so nothing was sent to Tractor. The details "
    "below say what went wrong."
)

# What a render task needs to run husk, denoise_batch and ffmpeg.
# Tractor's setenv envkey cuts a value at its first space, and none holds one.
# husk loads the renderer packages, MoonRay's among them, from
# HOUDINI_PACKAGE_DIR, so each blade gets its own build's paths.
ENV_VARS = (
    "HOUDINI_PACKAGE_DIR",
    "HOUDINI_PATH",
    "OCIO",
    "PATH",
    "PIXAR_LICENSE_FILE",
    "PXR_AR_DEFAULT_SEARCH_PATH",
    "PXR_PLUGINPATH_NAME",
    "RFHTREE",
    "RMAN_COLOR_CONFIG_DIR",
    "RMANTREE",
)
# The same on every machine: the license from /etc/profile.d, and RenderMan's
# install, which names no Houdini build.
MACHINE_VARS = ("PIXAR_LICENSE_FILE", "RFHTREE", "RMANTREE")
# Set by Open Shot for the substep sync HDA, and never saved in the hip.
SHOT_VARS = ("SHOT_SUBSTEPS",)
# A blade's own PATH has no Houdini, and not even /usr/bin. The Houdini folder
# is the link each machine points at its own build.
PATH = os.pathsep.join([str(Executables.hfs / "bin"), "/usr/bin", "/bin"])

log = logging.getLogger(__name__)


def check(node: hou.Node) -> None:
    """Refuse a node no Send can write."""
    if hou.hipFile.isNewFile():
        raise SendRefused("Save the hip file first. Its caches are written beside it.")
    # A cache saved into an asset keeps its buttons there.
    if node.isInsideLockedHDA():
        raise SendRefused(
            f"{node.path()} is inside the locked asset {_locked_asset(node).path()}, "
            "so Send can't change its parms. Choose Allow Editing of Contents on "
            "the asset, then Send again."
        )


def _locked_asset(node: hou.Node) -> hou.Node:
    asset = node.parent()
    while not asset.isLockedHDA():
        asset = asset.parent()
    return asset


def environment() -> dict[str, str]:
    """What a blade needs to load the hip as the session did.

    The launcher's values, not the session's: Houdini's packages add paths named
    for this workstation's build, and on a blade they add the blade's own.
    """
    env = {
        name: str(value)
        for name, value in launch.environment().items()
        if value is not None
    }
    env["PATH"] = PATH
    # hou.getenv sees what hou.putenv set, which os.environ doesn't.
    for name in (*MACHINE_VARS, *SHOT_VARS):
        if value := hou.getenv(name):
            env[name] = value
    return dict(sorted(env.items()))


def setenv() -> str:
    """The envkey that gives a render task its environment."""
    env = environment()
    # A variable the session lacks is left for the blade to choose.
    found = [f"{name}={env[name]}" for name in ENV_VARS if name in env]
    return " ".join(["setenv", *found])


def title(node: hou.Node, what: str = "") -> str:
    # The last folders of a shot's fx hip are <shot>/fx.
    hip = Path(hou.text.expandString("$HIP"))
    return f"{hip.parent.name} {hip.name}: {node.name()}{what}"


def spool(built: author.Job, title: str, unconfirmed: str = "") -> int | None:
    """The job's id, or None after warning that Tractor did not confirm it,
    ending with `unconfirmed`."""
    try:
        return built.spool(block=True)
    except Exception:
        # A lost reply looks the same as a refusal, and the job may already be
        # queued and reading its folders, so they stay.
        log.exception("Tractor did not confirm the job %s", title)
        hou.ui.displayMessage(
            "Tractor did not confirm the job, but it may still have been queued. "
            f'Look for "{title}" in Tractor before sending again.\n\n'
            f"{unconfirmed}".strip(),
            title="Tractor did not confirm the job",
            severity=hou.severityType.Warning,
            details=traceback.format_exc(),
        )
        return None


def refuse(message: str, details: str | None = None) -> None:
    hou.ui.displayMessage(
        message,
        title="Nothing was sent to Tractor",
        severity=hou.severityType.Error,
        details=details,
    )


def note(node: hou.Node, prefix: str, line: str) -> None:
    """Replace the line of the node's comment that starts with `prefix`,
    keeping the artist's own lines."""
    kept = [text for text in node.comment().splitlines() if not text.startswith(prefix)]
    node.setComment("\n".join([*kept, line]))
    node.setGenericFlag(hou.nodeFlag.DisplayComment, True)
