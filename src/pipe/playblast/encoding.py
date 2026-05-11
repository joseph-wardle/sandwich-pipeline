"""Compatibility shim — real implementation lives in `core.playblast.encoding`."""

from __future__ import annotations

import sys as _sys

import core.playblast.encoding as _real

# Re-bind the legacy `pipe.playblast.encoding` name onto the canonical `core.playblast.encoding`
# module object. After this assignment, `sys.modules["pipe.playblast.encoding"]` and
# `sys.modules["core.playblast.encoding"]` point at the same module, so subpath
# identity (`from pipe.playblast.encoding import X is core.playblast.encoding.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.encoding import *  # noqa: E402, F401, F403
