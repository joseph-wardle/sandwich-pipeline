"""Compatibility shim — real implementation lives in `core.telemetry.__main__`."""

from __future__ import annotations

import sys as _sys

import core.telemetry.__main__ as _real

# Re-bind the legacy `pipe.telemetry.__main__` name onto the canonical `core.telemetry.__main__`
# module object. After this assignment, `sys.modules["pipe.telemetry.__main__"]` and
# `sys.modules["core.telemetry.__main__"]` point at the same module, so subpath
# identity (`from pipe.telemetry.__main__ import X is core.telemetry.__main__.X`) holds.
_sys.modules[__name__] = _real

from core.telemetry.__main__ import *  # noqa: E402, F401, F403
