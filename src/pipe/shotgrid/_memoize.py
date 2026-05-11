"""Compatibility shim — real implementation lives in `core.shotgrid._memoize`."""

from __future__ import annotations

import sys as _sys

import core.shotgrid._memoize as _real

# Re-bind the legacy `pipe.shotgrid._memoize` name onto the canonical `core.shotgrid._memoize`
# module object. After this assignment, `sys.modules["pipe.shotgrid._memoize"]` and
# `sys.modules["core.shotgrid._memoize"]` point at the same module, so subpath
# identity (`from pipe.shotgrid._memoize import X is core.shotgrid._memoize.X`) holds.
_sys.modules[__name__] = _real

from core.shotgrid._memoize import *  # noqa: E402, F401, F403
