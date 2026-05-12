from __future__ import annotations

import logging
import platform
from pathlib import Path

from core.util.paths import get_shared_telemetry_spool_dir
from env import Executables
from framework.launcher import Launcher

log = logging.getLogger(__name__)


class SubstanceDesignerLauncher(Launcher):
    """Substance Designer outer-process launcher."""

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/dcc/substance_designer/launch.py`
        src_path = this_path.parents[2]
        repo_root = src_path.parent

        system = platform.system()

        env_vars = {
            "DCC": str(this_path.parent.name),
            "OCIO": str(repo_root / "resources/ocio/sandwich-v01/config.ocio"),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            "PYTHONPATH": str(src_path),
            "QT_PLUGIN_PATH": "",
        }

        if is_python_shell:
            raise NotImplementedError("Python shell is not supported for this DCC")

        launch_command = str(Executables.substance_designer)
        if not launch_command:
            raise NotImplementedError(
                f"The operating system {system} is not a supported OS for this DCC software"
            )

        launch_args = [
            "--config-file",
            str(this_path.parent / "site/lnd_configuration.sbscfg"),
            *(extra_args or []),
        ]

        super().__init__(launch_command, launch_args, env_vars)
