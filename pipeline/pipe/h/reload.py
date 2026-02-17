from __future__ import annotations

import logging

import hou

from pipe.util import reload_pipe as _reload_pipe

log = logging.getLogger(__name__)


def reload_pipe() -> None:
    """Reload pipeline Python modules and refresh Houdini HDA libraries."""
    _reload_pipe()

    try:
        hou.hda.reloadAllFiles()
    except Exception as exc:
        log.warning("Failed to reload Houdini HDA libraries: %s", exc)
