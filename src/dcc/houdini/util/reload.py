from __future__ import annotations

import logging

import hou

from core.util import reload_pipeline

log = logging.getLogger(__name__)


def reload_pipe() -> None:
    """Reload pipeline Python modules and refresh Houdini HDA libraries."""
    reload_pipeline()

    try:
        hou.hda.reloadAllFiles()
    except Exception as exc:
        log.warning("Failed to reload Houdini HDA libraries: %s", exc)
