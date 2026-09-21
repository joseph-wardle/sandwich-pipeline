"""Symlinks from the production tree into the cache mount."""

from __future__ import annotations

import logging
from pathlib import Path

from pipe.core.util.paths import get_cache_path, get_production_path

log = logging.getLogger(__name__)

RENDER_DIRNAME = "render"
SIM_DIRNAME = "geo"


def link_to_cache(directory: Path) -> None:
    """Make `directory` a symlink to its twin on the cache mount, creating the twin.

    A real directory that already holds files is left alone: moving an artist's
    files is not something to do behind their back mid-session.
    """
    twin = get_cache_path() / directory.relative_to(get_production_path())
    try:
        twin.mkdir(mode=0o770, parents=True, exist_ok=True)
        if directory.is_symlink():
            return
        if directory.is_dir():
            directory.rmdir()
        directory.symlink_to(twin, target_is_directory=True)
    except OSError as exc:
        # Never block an artist from opening their file over a cache link.
        log.warning(
            "Could not link %s to %s (%s). If it already holds files, move them "
            "there and delete the folder; it is linked the next time the file opens.",
            directory,
            twin,
            exc,
        )
