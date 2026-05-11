"""Compatibility shim — real implementation lives in `core.glui.dialogs`."""

from __future__ import annotations

import sys as _sys

import core.glui.dialogs as _real

# Re-bind the legacy `pipe.glui.dialogs` name onto the canonical `core.glui.dialogs`
# module object. After this assignment, `sys.modules["pipe.glui.dialogs"]` and
# `sys.modules["core.glui.dialogs"]` point at the same module, so subpath
# identity (`from pipe.glui.dialogs import X is core.glui.dialogs.X`) holds.
_sys.modules[__name__] = _real

from core.glui.dialogs import *  # noqa: E402, F401, F403
