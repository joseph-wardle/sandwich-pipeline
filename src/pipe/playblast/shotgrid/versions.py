"""Compatibility shim — real implementation lives in `core.playblast.shotgrid.versions`."""

from __future__ import annotations

import sys as _sys

import core.playblast.shotgrid.versions as _real

# Re-bind the legacy `pipe.playblast.shotgrid.versions` name onto the canonical `core.playblast.shotgrid.versions`
# module object. After this assignment, `sys.modules["pipe.playblast.shotgrid.versions"]` and
# `sys.modules["core.playblast.shotgrid.versions"]` point at the same module, so subpath
# identity (`from pipe.playblast.shotgrid.versions import X is core.playblast.shotgrid.versions.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.shotgrid.versions import *  # noqa: E402, F401, F403
