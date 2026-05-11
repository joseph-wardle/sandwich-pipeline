"""Compatibility shim — real implementation lives in `core.glui.progress`."""

from __future__ import annotations

import sys as _sys

import core.glui.progress as _real

# Re-bind the legacy `pipe.glui.progress` name onto the canonical `core.glui.progress`
# module object. After this assignment, `sys.modules["pipe.glui.progress"]` and
# `sys.modules["core.glui.progress"]` point at the same module, so subpath
# identity (`from pipe.glui.progress import X is core.glui.progress.X`) holds.
_sys.modules[__name__] = _real

from core.glui.progress import *  # noqa: E402, F401, F403
