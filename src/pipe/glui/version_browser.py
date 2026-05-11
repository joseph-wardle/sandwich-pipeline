"""Compatibility shim — real implementation lives in `core.glui.version_browser`."""

from __future__ import annotations

import sys as _sys

import core.glui.version_browser as _real

# Re-bind the legacy `pipe.glui.version_browser` name onto the canonical `core.glui.version_browser`
# module object. After this assignment, `sys.modules["pipe.glui.version_browser"]` and
# `sys.modules["core.glui.version_browser"]` point at the same module, so subpath
# identity (`from pipe.glui.version_browser import X is core.glui.version_browser.X`) holds.
_sys.modules[__name__] = _real

from core.glui.version_browser import *  # noqa: E402, F401, F403
