"""Compatibility shim — real implementation lives in `core.playblast.playblaster`."""

from __future__ import annotations

import sys as _sys

import core.playblast.playblaster as _real

# Re-bind the legacy `pipe.playblast.playblaster` name onto the canonical `core.playblast.playblaster`
# module object. After this assignment, `sys.modules["pipe.playblast.playblaster"]` and
# `sys.modules["core.playblast.playblaster"]` point at the same module, so subpath
# identity (`from pipe.playblast.playblaster import X is core.playblast.playblaster.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.playblaster import *  # noqa: E402, F401, F403
