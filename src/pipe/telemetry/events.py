"""Compatibility shim — real implementation lives in `core.telemetry.events`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.events as _real

# Re-bind the legacy `pipe.telemetry.events` name onto the canonical `core.telemetry.events`
# module object. After this assignment, `sys.modules["pipe.telemetry.events"]` and
# `sys.modules["core.telemetry.events"]` point at the same module, so subpath
# identity (`from pipe.telemetry.events import X is core.telemetry.events.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.events import *  # noqa: E402, F401, F403
