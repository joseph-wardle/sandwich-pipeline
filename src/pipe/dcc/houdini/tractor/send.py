"""What every Send shares: the checks before it, the environment its tasks run
in, spooling the job, and what it tells the artist."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import socket
import traceback
from pathlib import Path, PurePosixPath, PureWindowsPath

import hou
import tractor.api.author as author
from tractor.base import TrHttpRPC

from pipe.core.util import mounts
from pipe.dcc.houdini import launch
from pipe.dcc.houdini.tractor import SendRefused, engine

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
PIXAR_TREES = ("RFHTREE", "RMANTREE")
BLADE_PIXAR = PurePosixPath("/opt/pixar")
SYSTEM_PATH = ("/usr/bin", "/bin")
BLADE_PATH_SEPARATOR = ":"

log = logging.getLogger(__name__)
_tls_connected = False


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
    """What a blade needs to load the hip as the session did, naming the
    blade's own paths whichever machine sends.

    The launcher's values, not the session's: Houdini's packages add paths named
    for this workstation's build, and on a blade they add the blade's own.

    Raises:
        SendRefused: A value names a Windows path no blade can read.
    """
    env = {
        name: str(value)
        for name, value in launch.environment().items()
        if value is not None
    }
    for name in MACHINE_VARS:
        if value := hou.getenv(name):
            env[name] = value
    installs = _pixar_installs(env)
    blade = {
        name: BLADE_PATH_SEPARATOR.join(
            _blade_path(name, entry, installs) for entry in value.split(os.pathsep)
        )
        for name, value in env.items()
    }
    blade["PATH"] = BLADE_PATH_SEPARATOR.join(
        [str(_blade_houdini() / "bin"), *SYSTEM_PATH]
    )
    return dict(sorted(blade.items()))


def _pixar_installs(env: dict[str, str]) -> dict[PureWindowsPath, PurePosixPath]:
    """Each RenderMan install on this Windows machine's own disk, and the
    blade's install of it."""
    found = (PureWindowsPath(env[name]) for name in PIXAR_TREES if name in env)
    return {
        local: BLADE_PIXAR / local.name
        for local in found
        if mounts.is_windows_path(local)
    }


def _blade_path(
    name: str, entry: str, installs: dict[PureWindowsPath, PurePosixPath]
) -> str:
    """One entry of the variable `name`, as a blade names it."""
    if not mounts.is_windows_path(entry):
        return entry
    local = PureWindowsPath(entry)
    for install, blade in installs.items():
        if local.is_relative_to(install):
            return str(blade.joinpath(*local.relative_to(install).parts))
    try:
        return str(mounts.linux_path(entry))
    except mounts.NotOnShare:
        drives = ", ".join(f"{drive}:" for drive in mounts.DRIVE_SHARES)
        raise SendRefused(
            f"{name} names {entry}, which is on this computer's own disk, so no "
            "farm blade can read it. Send needs the pipeline and everything it "
            f"reads on a production drive ({drives}). Ask a pipeline TD to move it."
        ) from None


def _blade_houdini() -> PurePosixPath:
    """The blade's Houdini of the session's version."""
    major, minor, _build = hou.applicationVersion()
    return PurePosixPath(f"/opt/hfs{major}.{minor}")


def setenv() -> str:
    """The envkey that gives a render task its environment."""
    env = environment()
    # A variable the session lacks is left for the blade to choose.
    found = [f"{name}={env[name]}" for name in ENV_VARS if name in env]
    return " ".join(["setenv", *found])


def job_title(node: hou.Node, what: str = "") -> str:
    # The last folders of a shot's fx hip are <shot>/fx.
    hip = Path(hou.text.expandString("$HIP"))
    return f"{hip.parent.name} {hip.name}: {node.name()}{what}"


def spool(built: author.Job, title: str, unconfirmed: str = "") -> int | None:
    """The job's id, or None after warning that Tractor did not confirm it,
    ending with `unconfirmed`."""
    if platform.system() == "Windows":
        _connect_with_tls()
    try:
        return built.spool(block=True, owner=getpass.getuser())
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


def _connect_with_tls() -> None:
    """Make the Tractor client speak TLS, for this session."""
    global _tls_connected
    if _tls_connected:
        return
    connect = TrHttpRPC.TrHttpRPC.httpConnect
    context = engine.tls_context()

    def connect_with_tls(
        rpc: TrHttpRPC.TrHttpRPC,
    ) -> tuple[int, str | None, socket.socket | None]:
        errcode, outdata, plain = connect(rpc)
        if plain is None:
            return errcode, outdata, None
        try:
            secure = context.wrap_socket(plain, server_hostname=rpc.host)
        except OSError as error:
            plain.close()
            # The client's own shape for a failed connect, so its SpoolError
            # names the engine.
            return 999, f"TLS handshake with {rpc.host}:{rpc.port}: {error}", None
        return errcode, outdata, secure

    TrHttpRPC.TrHttpRPC.httpConnect = connect_with_tls
    _tls_connected = True


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
