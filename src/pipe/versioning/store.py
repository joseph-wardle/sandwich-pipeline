"""Compatibility shim — real implementation lives in `core.versioning.store`."""

from __future__ import annotations

import sys as _sys

import core.versioning.store as _real

# Re-bind the legacy `pipe.versioning.store` name onto the canonical `core.versioning.store`
# module object. After this assignment, `sys.modules["pipe.versioning.store"]` and
# `sys.modules["core.versioning.store"]` point at the same module, so subpath
# identity (`from pipe.versioning.store import X is core.versioning.store.X`) holds.
_sys.modules[__name__] = _real

from core.versioning.store import *  # noqa: E402, F401, F403
