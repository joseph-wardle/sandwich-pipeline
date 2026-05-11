"""Compatibility shim — real implementation lives in `core.versioning.service`."""

from __future__ import annotations

import sys as _sys

import core.versioning.service as _real

# Re-bind the legacy `pipe.versioning.service` name onto the canonical `core.versioning.service`
# module object. After this assignment, `sys.modules["pipe.versioning.service"]` and
# `sys.modules["core.versioning.service"]` point at the same module, so subpath
# identity (`from pipe.versioning.service import X is core.versioning.service.X`) holds.
_sys.modules[__name__] = _real

from core.versioning.service import *  # noqa: E402, F401, F403
