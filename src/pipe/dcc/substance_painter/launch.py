from __future__ import annotations

import logging
import platform
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typing

from pipe.core.color import ocio_env_vars
from pipe.core.util.paths import get_shared_telemetry_spool_dir
from env import Executables
from pipe.framework.launcher import Launcher

log = logging.getLogger(__name__)


class SubstancePainterLauncher(Launcher):
    """Substance Painter outer-process launcher."""

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/dcc/substance_painter/launch.py`
        src_path = this_path.parents[3]

        system = platform.system()

        env_vars: typing.Mapping[str, int | str | None] | None
        env_vars = {
            "DCC": str(this_path.parent.name),
            **ocio_env_vars(),
            "PIPE_LOG_LEVEL": log.getEffectiveLevel(),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            "PYTHONPATH": str(src_path),
            "QT_PLUGIN_PATH": "",
            "SUBSTANCE_PAINTER_PLUGINS_PATH": str(this_path.parent / "site"),
        }

        if is_python_shell:
            raise NotImplementedError("Python shell is not supported for this DCC")

        launch_command = str(Executables.substance_painter)
        if not launch_command:
            raise NotImplementedError(
                f"The operating system {system} is not a supported OS for this DCC software"
            )

        launch_args: list[str] = extra_args or []

        super().__init__(launch_command, launch_args, env_vars)
