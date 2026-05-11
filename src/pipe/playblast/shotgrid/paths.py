"""Compatibility shim — real implementation lives in `core.playblast.shotgrid.paths`."""

from __future__ import annotations

import sys as _sys

import core.playblast.shotgrid.paths as _real

# Re-bind the legacy `pipe.playblast.shotgrid.paths` name onto the canonical `core.playblast.shotgrid.paths`
# module object. After this assignment, `sys.modules["pipe.playblast.shotgrid.paths"]` and
# `sys.modules["core.playblast.shotgrid.paths"]` point at the same module, so subpath
# identity (`from pipe.playblast.shotgrid.paths import X is core.playblast.shotgrid.paths.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.shotgrid.paths import *  # noqa: E402, F401, F403
