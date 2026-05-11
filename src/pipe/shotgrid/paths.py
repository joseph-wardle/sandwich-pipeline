"""Compatibility shim — real implementation lives in `core.shotgrid.paths`."""

from __future__ import annotations

import sys as _sys

import core.shotgrid.paths as _real

# Re-bind the legacy `pipe.shotgrid.paths` name onto the canonical `core.shotgrid.paths`
# module object. After this assignment, `sys.modules["pipe.shotgrid.paths"]` and
# `sys.modules["core.shotgrid.paths"]` point at the same module, so subpath
# identity (`from pipe.shotgrid.paths import X is core.shotgrid.paths.X`) holds.
_sys.modules[__name__] = _real

from core.shotgrid.paths import *  # noqa: E402, F401, F403
