from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path

from core.util.util import (
    get_production_path,
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
        # `this_path` is `src/dcc/nuke/launch.py`; `parents[2]` is `src/`.
        # Phase 6 renames the variable and routes resources via
        # `<repo>/resources/`.
        pipe_path = this_path.parents[2]

        system = platform.system()

        env_vars = {
            "NUKE_PATH": str(resolve_mapped_path(this_path.parent / "site")),
            "OCIO": str(pipe_path / "lib/ocio/sandwich-v01/config.ocio"),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(pipe_path),
                    str(
                        get_production_path()
                        / f"../pipeline/pipeline/lib/python/3.9/{sys.platform}"
                    ),
                ]
            ),
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
