"""Compatibility shim — real implementation lives in `core.asset.paths`."""

from __future__ import annotations

import sys as _sys

import core.asset.paths as _real

# Re-bind the legacy `pipe.asset.paths` name onto the canonical `core.asset.paths`
# module object. After this assignment, `sys.modules["pipe.asset.paths"]` and
# `sys.modules["core.asset.paths"]` point at the same module, so subpath
# identity (`from pipe.asset.paths import X is core.asset.paths.X`) holds.
_sys.modules[__name__] = _real

from core.asset.paths import *  # noqa: E402, F401, F403
