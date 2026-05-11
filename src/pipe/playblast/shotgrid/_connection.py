"""Compatibility shim — real implementation lives in `core.playblast.shotgrid._connection`."""

from __future__ import annotations

import sys as _sys

import core.playblast.shotgrid._connection as _real

# Re-bind the legacy `pipe.playblast.shotgrid._connection` name onto the canonical `core.playblast.shotgrid._connection`
# module object. After this assignment, `sys.modules["pipe.playblast.shotgrid._connection"]` and
# `sys.modules["core.playblast.shotgrid._connection"]` point at the same module, so subpath
# identity (`from pipe.playblast.shotgrid._connection import X is core.playblast.shotgrid._connection.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.shotgrid._connection import *  # noqa: E402, F401, F403
