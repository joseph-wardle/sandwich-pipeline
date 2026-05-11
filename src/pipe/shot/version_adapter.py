"""Compatibility shim — real implementation lives in `core.shot.version_adapter`."""

from __future__ import annotations

import sys as _sys

import core.shot.version_adapter as _real

# Re-bind the legacy `pipe.shot.version_adapter` name onto the canonical `core.shot.version_adapter`
# module object. After this assignment, `sys.modules["pipe.shot.version_adapter"]` and
# `sys.modules["core.shot.version_adapter"]` point at the same module, so subpath
# identity (`from pipe.shot.version_adapter import X is core.shot.version_adapter.X`) holds.
_sys.modules[__name__] = _real

from core.shot.version_adapter import *  # noqa: E402, F401, F403
