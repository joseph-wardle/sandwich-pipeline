"""Compatibility shim — real implementation lives in `core.shotgrid.entities`."""

from __future__ import annotations

import sys as _sys

import core.shotgrid.entities as _real

# Re-bind the legacy `pipe.shotgrid.entities` name onto the canonical `core.shotgrid.entities`
# module object. After this assignment, `sys.modules["pipe.shotgrid.entities"]` and
# `sys.modules["core.shotgrid.entities"]` point at the same module, so subpath
# identity (`from pipe.shotgrid.entities import X is core.shotgrid.entities.X`) holds.
_sys.modules[__name__] = _real

from core.shotgrid.entities import *  # noqa: E402, F401, F403
