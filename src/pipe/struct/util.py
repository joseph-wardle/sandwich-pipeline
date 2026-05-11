"""Compatibility shim — real implementation lives in `core.struct.util`."""

from __future__ import annotations

import sys as _sys

import core.struct.util as _real

# Re-bind the legacy `pipe.struct.util` name onto the canonical `core.struct.util`
# module object. After this assignment, `sys.modules["pipe.struct.util"]` and
# `sys.modules["core.struct.util"]` point at the same module, so subpath
# identity (`from pipe.struct.util import X is core.struct.util.X`) holds.
_sys.modules[__name__] = _real

from core.struct.util import *  # noqa: E402, F401, F403
