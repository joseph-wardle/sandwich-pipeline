"""Compatibility shim — real implementation lives in `core.shotgrid.client`."""

from __future__ import annotations

import sys as _sys

import core.shotgrid.client as _real

# Re-bind the legacy `pipe.shotgrid.client` name onto the canonical `core.shotgrid.client`
# module object. After this assignment, `sys.modules["pipe.shotgrid.client"]` and
# `sys.modules["core.shotgrid.client"]` point at the same module, so subpath
# identity (`from pipe.shotgrid.client import X is core.shotgrid.client.X`) holds.
_sys.modules[__name__] = _real

from core.shotgrid.client import *  # noqa: E402, F401, F403
