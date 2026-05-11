"""Compatibility shim — real implementation lives in `core.telemetry.spool`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.spool as _real

# Re-bind the legacy `pipe.telemetry.spool` name onto the canonical `core.telemetry.spool`
# module object. After this assignment, `sys.modules["pipe.telemetry.spool"]` and
# `sys.modules["core.telemetry.spool"]` point at the same module, so subpath
# identity (`from pipe.telemetry.spool import X is core.telemetry.spool.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.spool import *  # noqa: E402, F401, F403
