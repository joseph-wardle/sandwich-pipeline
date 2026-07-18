"""Launch the viewer subprocess from a DCC (or the `pipe view` CLI)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipe.core.util.paths import get_repo_root, get_src_path


class ViewerSpawnError(Exception):
    """The viewer could not be launched. The message is artist-readable."""


# DCC-injected variables that would break the viewer's own Python/Qt runtime.
_UNSAFE_ENV_VARS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
)


def viewer_python() -> Path:
    """Return the viewer venv's Python, or raise with setup instructions."""
    venv = get_repo_root() / ".venv-viewer"
    is_windows = sys.platform == "win32"
    python = venv / ("Scripts/python.exe" if is_windows else "bin/python")
    if not python.exists():
        raise ViewerSpawnError(
            f"The playblast viewer is not installed (no {venv}).\n"
            "From the repository root, run:\n"
            "    uv venv .venv-viewer\n"
            "    uv pip install -p .venv-viewer -r pyproject.toml --group viewer"
        )
    return python


def viewer_command(spec_path: Path) -> list[str]:
    return [str(viewer_python()), "-m", "pipe.viewer", str(spec_path)]


def viewer_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _UNSAFE_ENV_VARS}
    # Pipeline code reaches the viewer the same way it reaches the DCCs.
    env["PYTHONPATH"] = str(get_src_path())
    return env


def spawn_viewer(spec_path: Path) -> None:
    """Open the viewer on `spec_path`, detached from this process.

    Detached so closing the DCC doesn't take an open review window with it.
    Raises `ViewerSpawnError` if the viewer venv is missing.
    """
    detach_kwargs: dict[str, Any]
    if sys.platform == "win32":
        detach_kwargs = {"creationflags": subprocess.DETACHED_PROCESS}
    else:
        detach_kwargs = {"start_new_session": True}
    subprocess.Popen(viewer_command(spec_path), env=viewer_env(), **detach_kwargs)


__all__ = [
    "ViewerSpawnError",
    "spawn_viewer",
    "viewer_command",
    "viewer_env",
    "viewer_python",
]
