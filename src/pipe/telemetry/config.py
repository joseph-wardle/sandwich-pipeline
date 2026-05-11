"""Compatibility shim — real implementation lives in `core.telemetry.config`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.config as _real

# Re-bind the legacy `pipe.telemetry.config` name onto the canonical `core.telemetry.config`
# module object. After this assignment, `sys.modules["pipe.telemetry.config"]` and
# `sys.modules["core.telemetry.config"]` point at the same module, so subpath
# identity (`from pipe.telemetry.config import X is core.telemetry.config.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.config import *  # noqa: E402, F401, F403
