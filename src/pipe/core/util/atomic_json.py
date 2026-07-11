"""Atomic JSON writes guarded by a cross-process file lock.

Shared by the versioning manifest store and the previs sequence manifest. Both
persist a single JSON document that concurrent tools may write, so both need the
same read-merge-write-under-lock discipline.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

_TEMP_SUFFIX = ".tmp"
_LOCK_SUFFIX = ".lock"


def write_json_atomic(path: Path, data: object) -> None:
    """Write ``data`` as JSON to ``path`` so a reader never sees a partial file.

    The bytes land in a sibling temp file that is renamed onto ``path``;
    ``os.replace`` is atomic within a filesystem. Callers sharing a path across
    processes should wrap their read-modify-write in :func:`json_write_lock` — a
    bare atomic write still loses updates when two writers interleave.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + _TEMP_SUFFIX)
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, path)


@contextmanager
def json_write_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive cross-process lock keyed on ``path`` for the block.

    The lock is a sibling ``<name>.lock`` file. ``filelock`` blocks until the
    lock is free, so concurrent writers serialize instead of clobbering.
    """
    lock_path = path.with_suffix(path.suffix + _LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path)):
        yield


__all__ = ["write_json_atomic", "json_write_lock"]
