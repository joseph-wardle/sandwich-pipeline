from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

from core.util.paths import (
    get_shared_telemetry_spool_dir,
    resolve_mapped_path,
)
from env import Executables
from framework.launcher import Launcher

log = logging.getLogger(__name__)


class NukeLauncher(Launcher):
    """Nuke outer-process launcher."""

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/dcc/nuke/launch.py`
        src_path = this_path.parents[2]
        repo_root = src_path.parent

        system = platform.system()

        env_vars = {
            # Root for vendored Nuke third-party (NukeSurvivalToolkit). Referenced
            # via [getenv DCC_NUKE_THIRD_PARTY] inside gizmo file knobs so paths
            # survive the repo's location moving.
            "DCC_NUKE_THIRD_PARTY": str(this_path.parent / "third_party"),
            "NUKE_PATH": str(resolve_mapped_path(this_path.parent / "site")),
            "OCIO": str(repo_root / "resources/ocio/sandwich-v01/config.ocio"),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            "PYTHONPATH": str(src_path),
            "QT_SCALE_FACTOR": os.getenv("NUKE_SCALE_FACTOR")
            if system == "Linux"
            else None,
        }

        launch_command = ""
        if is_python_shell:
            launch_command = str(Executables.nuke_python)
        else:
            launch_command = str(Executables.nuke)

        if is_python_shell:
            launch_args = extra_args or []
        else:
            launch_args = ["--nukex", *(extra_args or [])]

        super().__init__(launch_command, launch_args, env_vars)
