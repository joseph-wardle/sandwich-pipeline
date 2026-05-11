"""Compatibility shim — real implementation lives in `core.struct.timeline`."""

from __future__ import annotations

import sys as _sys

import core.struct.timeline as _real

# Re-bind the legacy `pipe.struct.timeline` name onto the canonical `core.struct.timeline`
# module object. After this assignment, `sys.modules["pipe.struct.timeline"]` and
# `sys.modules["core.struct.timeline"]` point at the same module, so subpath
# identity (`from pipe.struct.timeline import X is core.struct.timeline.X`) holds.
_sys.modules[__name__] = _real

from core.struct.timeline import *  # noqa: E402, F401, F403
