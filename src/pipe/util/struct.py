"""Compatibility shim — real implementation lives in `core.util.struct`."""

from __future__ import annotations

import sys as _sys

import core.util.struct as _real

# Re-bind the legacy `pipe.util.struct` name onto the canonical `core.util.struct`
# module object. After this assignment, `sys.modules["pipe.util.struct"]` and
# `sys.modules["core.util.struct"]` point at the same module, so subpath
# identity (`from pipe.util.struct import X is core.util.struct.X`) holds.
_sys.modules[__name__] = _real

from core.util.struct import *  # noqa: E402, F401, F403
