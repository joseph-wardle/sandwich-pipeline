from __future__ import annotations

import logging
from pathlib import Path

from pipe.core.color import ocio_env_vars
from env import Executables
from pipe.framework.launcher import Launcher

log = logging.getLogger(__name__)


class BlenderLauncher(Launcher):
    """Blender outer-process launcher."""

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/dcc/blender/launch.py`
        src_path = this_path.parents[3]
        repo_root = src_path.parent

        env_vars = {
            "PYTHONPATH": str(src_path),
            "BLENDER_CUSTOM_SPLASH": str(
                repo_root / "resources/splash/toaster_splash.png"
            ),
            "BLENDER_SYSTEM_EXTENSIONS": str(this_path.parent / "extensions"),
            "BLENDER_SYSTEM_SCRIPTS": str(this_path.parent / "site"),
            **ocio_env_vars(),
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
