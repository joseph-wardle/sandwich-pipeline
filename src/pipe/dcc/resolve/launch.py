from __future__ import annotations

import logging
import sys
from pathlib import Path

from env import Executables
from pipe.framework.launcher import Launcher

log = logging.getLogger(__name__)

USER_SCRIPTS_DIR = Path.home() / ".local/share/DaVinciResolve/Fusion/Scripts"
MENU_LINK = USER_SCRIPTS_DIR / "Utility" / "SKD"


class ResolveLauncher(Launcher):
    """DaVinci Resolve outer-process launcher (Linux)."""

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/pipe/dcc/resolve/launch.py`
        src_path = this_path.parents[3]
        self.menu_dir = this_path.parent / "site"

        if is_python_shell:
            resolve_root = Path(Executables.resolve).parents[1]
            scripting_api = resolve_root / "Developer/Scripting"
            env_vars = {
                "RESOLVE_SCRIPT_API": str(scripting_api),
                "RESOLVE_SCRIPT_LIB": str(resolve_root / "libs/Fusion/fusionscript.so"),
                "PYTHONPATH": f"{src_path}:{scripting_api / 'Modules'}",
            }
            super().__init__(sys.executable, extra_args or [], env_vars)
            return

        env_vars = {
            "PYTHON3HOME": sys.base_prefix,
            "PYTHONPATH": str(src_path),
        }
        launch_args = extra_args or []
        super().__init__(
            str(Executables.resolve), launch_args, env_vars, self._link_menu
        )

    def _link_menu(self) -> None:
        MENU_LINK.parent.mkdir(parents=True, exist_ok=True)
        if MENU_LINK.is_symlink():
            MENU_LINK.unlink()
        MENU_LINK.symlink_to(self.menu_dir, target_is_directory=True)
        log.info("Linked Resolve's SKD script menu to %s", self.menu_dir)
