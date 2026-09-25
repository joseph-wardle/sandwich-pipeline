from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typing

from pipe.core.color import ocio_env_vars
from pipe.core.util.paths import (
    get_production_path,
    get_shared_telemetry_spool_dir,
    resolve_mapped_path,
)
from env import Executables
from pipe.dcc.houdini.gallery import (
    DB_FILENAME,
    SESSION_DB_ENV,
    copy_db,
    production_db_path,
)
from pipe.framework.launcher import Launcher

log = logging.getLogger(__name__)

_TMPDIR = Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve() / str(
    os.getpid()
)
_TMPDIR.mkdir(0o755, exist_ok=True)


class HoudiniLauncher(Launcher):
    """Houdini outer-process launcher."""

    _assetdb_path: str

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/pipe/dcc/houdini/launch.py`
        src_path = this_path.parents[3]
        repo_root = src_path.parent
        third_party = this_path.parent / "third_party"

        self._assetdb_path = str(_TMPDIR / DB_FILENAME)

        env_vars: typing.Mapping[str, int | str | None] | None
        env_vars = {
            "DCC": str(this_path.parent.name),
            # Asset Gallery: each session gets a private copy of the production
            # DB, so Houdini never holds a SQLite file open over NFS.
            SESSION_DB_ENV: self._assetdb_path,
            "HOUDINI_BACKUP_DIR": "./.backup",
            "HOUDINI_COREDUMP": 1,
            "HOUDINI_DSO_ERROR": 2 if log.isEnabledFor(logging.DEBUG) else None,
            "HOUDINI_MAX_BACKUP_FILES": 20,
            "HOUDINI_NO_ENV_FILE_OVERRIDES": 1,
            "HOUDINI_NO_START_PAGE_SPLASH": 1,
            "HOUDINI_OTLSCAN_PATH": os.pathsep.join(
                [
                    str(p)
                    for p in resolve_mapped_path(
                        get_production_path() / "hda"
                    ).iterdir()
                ]
                + ["&"]
            ),
            "HOUDINI_PACKAGE_DIR": str(
                resolve_mapped_path(this_path.parent / "site" / "packages")
            ),
            # Package loading debug logging
            "HOUDINI_PACKAGE_VERBOSE": 1 if log.isEnabledFor(logging.DEBUG) else None,
            # Houdini Path — Houdini auto-scans each entry for otls/,
            # toolbar/, scripts/, python3.11libs/, etc. We put site/ on the
            # path directly (packages are handled via HOUDINI_PACKAGE_DIR).
            "HOUDINI_PATH": os.pathsep.join(
                [
                    str(resolve_mapped_path(this_path.parent / "site")),
                    str(repo_root / "resources/usd/kinds"),
                    "&",
                ]
            ),
            "HOUDINI_SPLASH_FILE": str(
                repo_root / "resources/splash/panini_splash.png"
            ),
            # Kept for any HSCRIPT that still references $HSITE; not load-bearing
            "HSITE": str(resolve_mapped_path(this_path.parent / "site")),
            "JOB": str(resolve_mapped_path(get_production_path())),
            # Ensure LD_LIBRARY_PATH is unset to allow nesting pipe instances
            "LD_LIBRARY_PATH": None,
            **ocio_env_vars(),
            "PIPE_LOG_LEVEL": log.getEffectiveLevel(),
            # Pipeline HDAs read shared assets (HDRIs, reference assets) here
            "PIPE_RESOURCES": str(resolve_mapped_path(repo_root / "resources")),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            # Root for vendored Houdini packages (MOPS, LYNX, axiom, ae_SVG)
            "DCC_HOUDINI_THIRD_PARTY": str(third_party),
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
            # Add pipeline modules to Python path
            "PYTHONPATH": os.pathsep.join(
                [
                    str(resolve_mapped_path(src_path)),
                    # Add $RMANTREE/bin to PYTHONPATH for the Tractor PDG scheduler
                    os.environ.get("RMANTREE", "") + "/bin",
                ]
            ),
            "QT_PREFERRED_BINDING": None,
            # Explicitly set Tractor location
            "TRACTOR_ENGINE": "tractor-engine.cs.byu.edu:443",
        }

        launch_command = ""
        if is_python_shell:
            launch_command = str(Executables.hython)
        else:
            launch_command = str(Executables.houdini)

        if is_python_shell:
            launch_args = extra_args or []
        else:
            launch_args = ["-foreground", *(extra_args or [])]

        super().__init__(
            launch_command, launch_args, env_vars, lambda: self._set_up_asset_gallery()
        )

    def _set_up_asset_gallery(self) -> None:
        for stale in _TMPDIR.glob(f"{DB_FILENAME}*"):
            stale.unlink()
        if production_db_path().is_file():
            copy_db(production_db_path(), Path(self._assetdb_path))
        else:
            log.warning(
                "No Asset Gallery at %s; this session starts with an empty one",
                production_db_path(),
            )
