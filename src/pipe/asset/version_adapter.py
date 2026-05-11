"""Compatibility shim — real implementation lives in `core.asset.version_adapter`."""

from __future__ import annotations

import sys as _sys

import core.asset.version_adapter as _real

# Re-bind the legacy `pipe.asset.version_adapter` name onto the canonical `core.asset.version_adapter`
# module object. After this assignment, `sys.modules["pipe.asset.version_adapter"]` and
# `sys.modules["core.asset.version_adapter"]` point at the same module, so subpath
# identity (`from pipe.asset.version_adapter import X is core.asset.version_adapter.X`) holds.
_sys.modules[__name__] = _real

from core.asset.version_adapter import *  # noqa: E402, F401, F403
