"""Compatibility shim — real implementation lives in `core.util.util`."""

from __future__ import annotations

import sys as _sys

import core.util.util as _real

# Re-bind the legacy `pipe.util.util` name onto the canonical `core.util.util`
# module object. After this assignment, `sys.modules["pipe.util.util"]` and
# `sys.modules["core.util.util"]` point at the same module, so subpath
# identity (`from pipe.util.util import X is core.util.util.X`) holds.
_sys.modules[__name__] = _real

from core.util.util import *  # noqa: E402, F401, F403
