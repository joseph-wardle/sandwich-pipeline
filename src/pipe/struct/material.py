"""Compatibility shim — real implementation lives in `core.struct.material`."""

from __future__ import annotations

import sys as _sys

import core.struct.material as _real

# Re-bind the legacy `pipe.struct.material` name onto the canonical `core.struct.material`
# module object. After this assignment, `sys.modules["pipe.struct.material"]` and
# `sys.modules["core.struct.material"]` point at the same module, so subpath
# identity (`from pipe.struct.material import X is core.struct.material.X`) holds.
_sys.modules[__name__] = _real

from core.struct.material import *  # noqa: E402, F401, F403
