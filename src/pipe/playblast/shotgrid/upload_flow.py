"""Compatibility shim — real implementation lives in `core.playblast.shotgrid.upload_flow`."""

from __future__ import annotations

import sys as _sys

import core.playblast.shotgrid.upload_flow as _real

# Re-bind the legacy `pipe.playblast.shotgrid.upload_flow` name onto the canonical `core.playblast.shotgrid.upload_flow`
# module object. After this assignment, `sys.modules["pipe.playblast.shotgrid.upload_flow"]` and
# `sys.modules["core.playblast.shotgrid.upload_flow"]` point at the same module, so subpath
# identity (`from pipe.playblast.shotgrid.upload_flow import X is core.playblast.shotgrid.upload_flow.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.shotgrid.upload_flow import *  # noqa: E402, F401, F403
