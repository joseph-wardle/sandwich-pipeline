"""Compatibility shim — real implementation lives in `core.versioning.model`."""

from __future__ import annotations

import sys as _sys

import core.versioning.model as _real

# Re-bind the legacy `pipe.versioning.model` name onto the canonical `core.versioning.model`
# module object. After this assignment, `sys.modules["pipe.versioning.model"]` and
# `sys.modules["core.versioning.model"]` point at the same module, so subpath
# identity (`from pipe.versioning.model import X is core.versioning.model.X`) holds.
_sys.modules[__name__] = _real

from core.versioning.model import *  # noqa: E402, F401, F403
