"""Compatibility shim — real implementation lives in `core.playblast.naming`."""

from __future__ import annotations

import sys as _sys

import core.playblast.naming as _real

# Re-bind the legacy `pipe.playblast.naming` name onto the canonical `core.playblast.naming`
# module object. After this assignment, `sys.modules["pipe.playblast.naming"]` and
# `sys.modules["core.playblast.naming"]` point at the same module, so subpath
# identity (`from pipe.playblast.naming import X is core.playblast.naming.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.naming import *  # noqa: E402, F401, F403
