from __future__ import annotations

import logging
import os
from pathlib import Path

from env import Executables
from framework.launcher import Launcher

log = logging.getLogger(__name__)


class BlenderLauncher(Launcher):
    """Blender outer-process launcher."""

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # `this_path` is `src/dcc/blender/launch.py`; `parents[2]` is `src/`.
        # Phase 6 renames the variable and routes resources via
        # `<repo>/resources/`.
        pipe_path = this_path.parents[2]

        env_vars = {
            "PYTHONPATH": os.pathsep.join([str(pipe_path)]),
            "BLENDER_CUSTOM_SPLASH": str(pipe_path / "lib/splash/toaster_splash.png"),
            "BLENDER_SYSTEM_EXTENSIONS": str(this_path.parent / "extensions"),
            "BLENDER_SYSTEM_SCRIPTS": str(this_path.parent / "site"),
            "OCIO": str(pipe_path / "lib/ocio/sandwich-v01/config.ocio"),
        }

        launch_command = str(Executables.blender)
        if is_python_shell:
            launch_args = [
                "--python-console",
                "--background",
                "--python-use-system-env",
                *(extra_args or []),
            ]
        else:
            launch_args = ["--python-use-system-env", *(extra_args or [])]

        super().__init__(launch_command, launch_args, env_vars)
