"""Compatibility shim — real implementation lives in `core.telemetry.local_stack`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.local_stack as _real

# Re-bind the legacy `pipe.telemetry.local_stack` name onto the canonical `core.telemetry.local_stack`
# module object. After this assignment, `sys.modules["pipe.telemetry.local_stack"]` and
# `sys.modules["core.telemetry.local_stack"]` point at the same module, so subpath
# identity (`from pipe.telemetry.local_stack import X is core.telemetry.local_stack.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.local_stack import *  # noqa: E402, F401, F403
