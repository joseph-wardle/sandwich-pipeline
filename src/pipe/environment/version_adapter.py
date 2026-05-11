"""Compatibility shim — real implementation lives in `core.environment.version_adapter`."""

from __future__ import annotations

import sys as _sys

import core.environment.version_adapter as _real

# Re-bind the legacy `pipe.environment.version_adapter` name onto the canonical `core.environment.version_adapter`
# module object. After this assignment, `sys.modules["pipe.environment.version_adapter"]` and
# `sys.modules["core.environment.version_adapter"]` point at the same module, so subpath
# identity (`from pipe.environment.version_adapter import X is core.environment.version_adapter.X`) holds.
_sys.modules[__name__] = _real

from core.environment.version_adapter import *  # noqa: E402, F401, F403
