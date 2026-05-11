"""Compatibility shim — real implementation lives in `core.telemetry.emit`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.emit as _real

# Re-bind the legacy `pipe.telemetry.emit` name onto the canonical `core.telemetry.emit`
# module object. After this assignment, `sys.modules["pipe.telemetry.emit"]` and
# `sys.modules["core.telemetry.emit"]` point at the same module, so subpath
# identity (`from pipe.telemetry.emit import X is core.telemetry.emit.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.emit import *  # noqa: E402, F401, F403
