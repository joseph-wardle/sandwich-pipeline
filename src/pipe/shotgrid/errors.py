"""Compatibility shim — real implementation lives in `core.shotgrid.errors`."""

from __future__ import annotations

import sys as _sys

import core.shotgrid.errors as _real

# Re-bind the legacy `pipe.shotgrid.errors` name onto the canonical `core.shotgrid.errors`
# module object. After this assignment, `sys.modules["pipe.shotgrid.errors"]` and
# `sys.modules["core.shotgrid.errors"]` point at the same module, so subpath
# identity (`from pipe.shotgrid.errors import X is core.shotgrid.errors.X`) holds.
_sys.modules[__name__] = _real

from core.shotgrid.errors import *  # noqa: E402, F401, F403
