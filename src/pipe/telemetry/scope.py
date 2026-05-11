"""Compatibility shim — real implementation lives in `core.telemetry.scope`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.scope as _real

# Re-bind the legacy `pipe.telemetry.scope` name onto the canonical `core.telemetry.scope`
# module object. After this assignment, `sys.modules["pipe.telemetry.scope"]` and
# `sys.modules["core.telemetry.scope"]` point at the same module, so subpath
# identity (`from pipe.telemetry.scope import X is core.telemetry.scope.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.scope import *  # noqa: E402, F401, F403
