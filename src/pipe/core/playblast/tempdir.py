"""Single source of truth for the playblast temp directory. Used by the
`Playblaster` base to stage image sequences + encoded movies, and by the
playblast dialogs to seed the Custom Folder field."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_playblast_tempdir() -> Path:
    """Return the directory for staging playblast intermediates.

    Honors `$TMPDIR` first, then `$TEMP`, falling back to a relative
    `tmp/` directory resolved against the current working directory.
    Always returns an absolute path.
    """
    return Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve()


__all__ = ["resolve_playblast_tempdir"]
