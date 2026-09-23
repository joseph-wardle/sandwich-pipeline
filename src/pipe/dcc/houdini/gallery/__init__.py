"""The Houdini Asset Gallery.

The production DB holds one item per published component USD, keyed by that
file's path, and only this package writes it: `publish` upserts the asset it
just exported and `rebuild` regenerates the whole catalogue from the asset
directories on disk.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from filelock import FileLock

from pipe.core.util.paths import get_production_path

DB_FILENAME = "assetGallery.db"
THUMBNAIL_DIRNAME = ".thumbnails"
LOCK_TIMEOUT_SECONDS = 40
SESSION_DB_ENV = "HOUDINI_ASSETGALLERY_DB_FILE"


def production_db_path() -> Path:
    return get_production_path() / "asset" / DB_FILENAME


def thumbnail_path(export_path: Path) -> Path:
    """Where the gallery thumbnail for a published component lives on disk."""
    return export_path.parent / THUMBNAIL_DIRNAME / f"{export_path.stem}.png"


def db_lock(db_path: Path) -> FileLock:
    """The cross-host lock held by `db.transaction` and `copy_db`.

    SQLite's write-ahead log is only coherent between processes on one host,
    so readers on other hosts must not see one in progress either.
    """
    return FileLock(f"{db_path}.lock", timeout=LOCK_TIMEOUT_SECONDS, mode=0o775)


def copy_db(db_path: Path, dest: Path) -> None:
    """Write a complete, consistent copy of `db_path` to `dest`."""
    with db_lock(db_path):
        with (
            closing(sqlite3.connect(db_path)) as source,
            closing(sqlite3.connect(dest)) as target,
        ):
            source.backup(target)


__all__ = [
    "DB_FILENAME",
    "SESSION_DB_ENV",
    "THUMBNAIL_DIRNAME",
    "copy_db",
    "db_lock",
    "production_db_path",
    "thumbnail_path",
]
