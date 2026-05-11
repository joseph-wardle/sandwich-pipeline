"""Compatibility shim — real implementation lives in `core.telemetry.ingester`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.ingester as _real

# Re-bind the legacy `pipe.telemetry.ingester` name onto the canonical `core.telemetry.ingester`
# module object. After this assignment, `sys.modules["pipe.telemetry.ingester"]` and
# `sys.modules["core.telemetry.ingester"]` point at the same module, so subpath
# identity (`from pipe.telemetry.ingester import X is core.telemetry.ingester.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.ingester import *  # noqa: E402, F401, F403
