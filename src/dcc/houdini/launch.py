from __future__ import annotations

import atexit
import filecmp
import logging
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock

if TYPE_CHECKING:
    import typing

from core.util.paths import (
    get_production_path,
    get_shared_telemetry_spool_dir,
    resolve_mapped_path,
)
from env import Executables
from framework.launcher import Launcher

log = logging.getLogger(__name__)

_PROD_DB = str(get_production_path() / "asset/assetGallery.db")
_TMPDIR = Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve() / str(
    os.getpid()
)
_TMPDIR.mkdir(0o755, exist_ok=True)


class HoudiniLauncher(Launcher):
    """Houdini outer-process launcher."""

    _assetdb_path: str
    _orig_assetdb_path: str

    def __init__(
        self, is_python_shell: bool = False, extra_args: list[str] | None = None
    ) -> None:
        this_path = Path(__file__).resolve()
        # this_path = `<repo>/src/dcc/houdini/launch.py`
        src_path = this_path.parents[2]
        repo_root = src_path.parent
        third_party = this_path.parent / "third_party"

        self._assetdb_path = str(_TMPDIR / "assetGallery.db")
        self._orig_assetdb_path = str(_TMPDIR / "assetGallery_orig.db")

        env_vars: typing.Mapping[str, int | str | None] | None
        env_vars = {
            "DCC": str(this_path.parent.name),
            # Asset Gallery: Houdini writes to a per-session temp copy so the
            # prod DB is never held open by Houdini. Changes are merged back to
            # prod on clean exit via _merge_asset_gallery_changes().
            "HOUDINI_ASSETGALLERY_DATA_SOURCE": self._assetdb_path,
            "HOUDINI_ASSETGALLERY_DB_FILE": self._assetdb_path,
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
            # Package loading debug logging
            "HOUDINI_PACKAGE_VERBOSE": 1 if log.isEnabledFor(logging.DEBUG) else None,
            # Houdini Path — Houdini auto-scans each entry for otls/,
            # toolbar/, packages/, scripts/, python3.11libs/, etc. We put
            # site/ on the path directly
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
            "OCIO": str(repo_root / "resources/ocio/sandwich-v01/config.ocio"),
            "PIPE_LOG_LEVEL": log.getEffectiveLevel(),
            "PIPE_TELEMETRY_SPOOL_DIR": str(get_shared_telemetry_spool_dir()),
            # Root for vendored Houdini packages (MOPS, LYNX, axiom, tlops, ae_SVG)
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
            "RMAN_COLOR_CONFIG_DIR": str(repo_root / "resources/ocio/sandwich-v01"),
            # Force Qt5 bindings in Houdini to avoid Qt6/PySide6 conflicts
            "QT_PREFERRED_BINDING": "PySide2",
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
        for f in _TMPDIR.glob("assetGallery.*"):
            f.unlink()

        shutil.copy(_PROD_DB, self._assetdb_path)
        shutil.copy(self._assetdb_path, self._orig_assetdb_path)

        atexit.register(lambda: self._merge_asset_gallery_changes())

    def _merge_asset_gallery_changes(self) -> None:
        # Guard against the OS cleaning up $TMPDIR while Houdini was running.
        if (
            not Path(self._assetdb_path).exists()
            or not Path(self._orig_assetdb_path).exists()
        ):
            log.warning(
                "Asset gallery temp files missing; skipping merge into prod DB."
            )
            return

        with closing(sqlite3.connect(self._assetdb_path)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # Skip merge if the session made no gallery changes.
        filecmp.clear_cache()
        if filecmp.cmp(self._orig_assetdb_path, self._assetdb_path, shallow=False):
            return

        log.info("Merging asset gallery changes into prod DB")

        lock_path = _PROD_DB + ".lock"

        lock = FileLock(lock_path, mode=0o775)

        # merge local modifications into the prod database
        with lock.acquire(timeout=40), closing(sqlite3.connect(_PROD_DB)) as conn:
            cur = conn.cursor()
            with (
                attach_db(cur, self._assetdb_path) as MODIFIED,
                attach_db(cur, self._orig_assetdb_path) as ORIGINAL,
                conn,
            ):
                cur.execute("BEGIN")
                table_query = (
                    f"SELECT * from {MODIFIED}.sqlite_master WHERE type='table'"
                )
                for table in (nm for tp, nm, *_ in cur.execute(table_query)):
                    # find the insertion point we left off at
                    cur.execute(f"SELECT MAX(id) FROM {ORIGINAL}.{table}")
                    last_id = cur.fetchone()[0]

                    if isinstance(last_id, int):  # if there are already entries
                        # update any changes to existing entries
                        cur.execute(
                            f"INSERT OR REPLACE INTO {table} "
                            f"SELECT * FROM {MODIFIED}.{table} WHERE id <= {last_id} "
                            + (
                                "AND marked_for_deletion = 0"
                                if table == "items"
                                else ""
                            )
                        )
                        # find all the non-id columns
                        cur.execute(f"SELECT * FROM {ORIGINAL}.{table}")
                        columns_no_id = [d[0] for d in cur.description if d[0] != "id"]
                        columns_str = ", ".join(columns_no_id)
                        # insert any new entries, will generate a new ID
                        cur.execute(
                            f"INSERT INTO {table} ({columns_str}) "
                            f"SELECT {columns_str} FROM {MODIFIED}.{table} WHERE id > {last_id} "
                            + (
                                "AND marked_for_deletion = 0"
                                if table == "items"
                                else ""
                            )
                        )
                    else:
                        # insert any new entries
                        cur.execute(
                            f"INSERT INTO {table} "
                            f"SELECT * FROM {MODIFIED}.{table} "
                            + (
                                "WHERE marked_for_deletion = 0"
                                if table == "items"
                                else ""
                            )
                        )

        # Flush the write-ahead log into the main DB file so no pending
        # changes are left in a .wal sidecar that could confuse future readers.
        with closing(sqlite3.connect(_PROD_DB)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # clean up
        for f in _TMPDIR.glob("assetGallery.*"):
            f.unlink()
        with suppress(OSError):
            _TMPDIR.rmdir()


@contextmanager
def attach_db(cur: sqlite3.Cursor, path: str):
    name = "".join(c for c in path if c.isalpha())
    cur.execute(f"ATTACH DATABASE '{path}' AS {name}")

    try:
        yield name
    finally:
        cur.execute(f"DETACH DATABASE {name}")
