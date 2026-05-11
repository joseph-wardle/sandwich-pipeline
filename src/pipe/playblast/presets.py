"""Compatibility shim — real implementation lives in `core.playblast.presets`."""

from __future__ import annotations

import sys as _sys

import core.playblast.presets as _real

# Re-bind the legacy `pipe.playblast.presets` name onto the canonical `core.playblast.presets`
# module object. After this assignment, `sys.modules["pipe.playblast.presets"]` and
# `sys.modules["core.playblast.presets"]` point at the same module, so subpath
# identity (`from pipe.playblast.presets import X is core.playblast.presets.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.presets import *  # noqa: E402, F401, F403
