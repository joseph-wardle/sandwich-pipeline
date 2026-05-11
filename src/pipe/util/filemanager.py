"""Compatibility shim — real implementation lives in `core.util.filemanager`."""

from __future__ import annotations

import sys as _sys

import core.util.filemanager as _real

# Re-bind the legacy `pipe.util.filemanager` name onto the canonical `core.util.filemanager`
# module object. After this assignment, `sys.modules["pipe.util.filemanager"]` and
# `sys.modules["core.util.filemanager"]` point at the same module, so subpath
# identity (`from pipe.util.filemanager import X is core.util.filemanager.X`) holds.
_sys.modules[__name__] = _real

from core.util.filemanager import *  # noqa: E402, F401, F403
