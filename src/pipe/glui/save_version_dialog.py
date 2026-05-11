"""Compatibility shim — real implementation lives in `core.glui.save_version_dialog`."""

from __future__ import annotations

import sys as _sys

import core.glui.save_version_dialog as _real

# Re-bind the legacy `pipe.glui.save_version_dialog` name onto the canonical `core.glui.save_version_dialog`
# module object. After this assignment, `sys.modules["pipe.glui.save_version_dialog"]` and
# `sys.modules["core.glui.save_version_dialog"]` point at the same module, so subpath
# identity (`from pipe.glui.save_version_dialog import X is core.glui.save_version_dialog.X`) holds.
_sys.modules[__name__] = _real

from core.glui.save_version_dialog import *  # noqa: E402, F401, F403
