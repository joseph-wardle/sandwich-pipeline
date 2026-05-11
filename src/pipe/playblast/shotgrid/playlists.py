"""Compatibility shim — real implementation lives in `core.playblast.shotgrid.playlists`."""

from __future__ import annotations

import sys as _sys

import core.playblast.shotgrid.playlists as _real

# Re-bind the legacy `pipe.playblast.shotgrid.playlists` name onto the canonical `core.playblast.shotgrid.playlists`
# module object. After this assignment, `sys.modules["pipe.playblast.shotgrid.playlists"]` and
# `sys.modules["core.playblast.shotgrid.playlists"]` point at the same module, so subpath
# identity (`from pipe.playblast.shotgrid.playlists import X is core.playblast.shotgrid.playlists.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.shotgrid.playlists import *  # noqa: E402, F401, F403
