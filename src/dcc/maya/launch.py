from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typing

from core.util.paths import (
    get_production_path,
    get_rig_build_path,
    get_shared_telemetry_spool_dir,
)
from env import Executables
from framework.launcher import Launcher

log = logging.getLogger(__name__)


class MayaLauncher(Launcher):
    """Maya outer-process launcher."""

    shelf_path: str
    splash_path: str

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/dcc/maya/launch.py`
        src_path = this_path.parents[2]
        repo_root = src_path.parent
        third_party = this_path.parent / "third_party"
        site_path = this_path.parent / "site"
        rig_build_path = get_rig_build_path()
        system = platform.system()

        self.shelf_path = str(
            Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve() / "shelves"
        )
        self.splash_path = str(
            Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve()
            / "maya_splash"
        )

        env_vars: typing.Mapping[str, int | str | None] | None

        module_paths = []
        # add the production path plus the folders where we put our modules
        module_paths.append(str(get_production_path() / "maya/module"))
        module_paths.append(str(third_party / "y-rig/third_party/mgear/release"))
        # adding the preexisting path, if it exists
        existing_module_path = os.environ.get("MAYA_MODULE_PATH")
        if existing_module_path:
            module_paths.extend(existing_module_path.split(os.pathsep))

        env_vars = {
            "DCC": str(this_path.parent.name),
            "DWPICKER_PROJECT_DIRECTORY": str(get_production_path() / "pickers"),
            "MAYA_SHELF_PATH": self.shelf_path,
            "MAYAUSD_EXPORT_MAP1_AS_PRIMARY_UV_SET": 1,
            "MAYAUSD_IMPORT_PRIMARY_UV_SET_AS_MAP1": 1,
            "MAYA_MODULE_PATH": os.pathsep.join(module_paths),
            "MAYA_PLUG_IN_PATH": str(site_path),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            # PYTHONPATH:
            #   1. src/ — so framework, core, dcc, env, env_sg are importable
            #   2. site/ — Maya scans sys.path literally for `userSetup.py`
            #   3. third_party/ — flat vendored libs (mayacapture, dwpicker,
            #      etc) are imported by their top-level package name
            #   4. third_party/studiolibrary/src — studiolibrary's vendored
            #      layout nests its package under an extra src/ directory
            "PYTHONPATH": os.pathsep.join(
                [
                    str(src_path),
                    str(site_path),
                    str(third_party),
                    str(third_party / "studiolibrary/src"),
                ]
            ),
            "OCIO": str(repo_root / "resources/ocio/sandwich-v01/config.ocio"),
            "QT_FONT_DPI": os.getenv("MAYA_FONT_DPI") if system == "Linux" else None,
            "QT_PLUGIN_PATH": None,
            # Configure Asset Resolver
            "PXR_AR_DEFAULT_SEARCH_PATH": os.pathsep.join(
                [
                    str(get_production_path()),
                ]
            ),
            # USD Plugins
            "PXR_PLUGINPATH_NAME": os.pathsep.join(
                [
                    str(repo_root / "resources/usd/kinds"),
                    os.environ.get("PXR_PLUGINPATH_NAME", ""),
                ]
            ),
            # Icons
            "XBMLANGPATH": os.pathsep.join(
                [
                    str(pth) + ("/%B" if system == "Linux" else "")
                    for pth in [
                        Path(self.splash_path),
                        third_party / "studiolibrary/src/studiolibrary/resource/icons",
                        repo_root / "resources/icon",
                        repo_root / "resources/splash",
                    ]
                ]
            ),
            # Y-Rig Custom Components and Root Build Directory
            "MGEAR_SHIFTER_COMPONENT_PATH": str(
                third_party / "y-rig/shifter/components/"
            ),
            "MGEAR_SHIFTER_CUSTOMSTEP_PATH": str(rig_build_path),
        }

        launch_command = ""
        launch_args: list[str] = []
        if is_python_shell:
            launch_command = str(Executables.mayapy)
            cmd_str = ""
            extra_args_preamble = []

            print(extra_args)

            if extra_args:
                # extract the cmd arg so we can append it to everything else
                try:
                    cmd_flag_index = next(
                        (
                            i
                            for i, f in enumerate(extra_args)
                            if (f[0] == "-") and (f[-1] == "c")
                        )
                    )
                    cmd_str = extra_args[cmd_flag_index + 1]
                    if len(extra_args[cmd_flag_index]) > 2:
                        cmd_str_other_flags = ["-" + extra_args[cmd_flag_index][1:-1]]
                    else:
                        cmd_str_other_flags = []

                    extra_args_preamble = (
                        extra_args[:cmd_flag_index]
                        + extra_args[cmd_flag_index + 2 :]
                        + cmd_str_other_flags
                    )
                except StopIteration:
                    pass

            launch_args = extra_args_preamble + [
                "-ic",
                ";".join(
                    [
                        "import atexit",
                        "import maya.standalone",
                        "maya.standalone.initialize()",
                        "atexit.register(maya.standalone.uninitialize)",
                        cmd_str,
                    ]
                ),
            ]
        else:
            launch_command = str(Executables.maya)
            if system == "Linux":
                launch_args.extend(("-name", "Mayo"))
            if extra_args:
                launch_args.extend(extra_args)

        super().__init__(launch_command, launch_args, env_vars, self._pre_launch_tasks)

    def _pre_launch_tasks(self) -> None:
        self.set_up_shelf_path()
        self.set_up_splash_path()

    def set_up_shelf_path(self) -> None:
        prod_dir = str(Path(__file__).parent / "site/shelves")
        local_dir = self.shelf_path

        shutil.copytree(prod_dir, local_dir, dirs_exist_ok=True)

    def set_up_splash_path(self) -> None:
        splash_dir = Path(self.splash_path)
        splash_dir.mkdir(parents=True, exist_ok=True)

        repo_root = Path(__file__).resolve().parents[3]
        src = repo_root / "resources/splash/mayo_splash.png"
        if not src.exists():
            log.warning("Missing Maya splash image at %s", src)
            return

        target_names = [
            "MayaStartupImage.png",
            "MayaStartupImage_150.png",
            "MayaStartupImage_200.png",
            "MayaEDUStartupImage.png",
            "MayaEDUStartupImage_150.png",
            "MayaEDUStartupImage_200.png",
        ]
        for name in target_names:
            dest = splash_dir / name
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            try:
                dest.symlink_to(src)
            except OSError:
                shutil.copy2(src, dest)
