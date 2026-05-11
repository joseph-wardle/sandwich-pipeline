"""Compatibility shim — real implementation lives in `core.playblast.tempdir`."""

from __future__ import annotations

import sys as _sys

import core.playblast.tempdir as _real

# Re-bind the legacy `pipe.playblast.tempdir` name onto the canonical `core.playblast.tempdir`
# module object. After this assignment, `sys.modules["pipe.playblast.tempdir"]` and
# `sys.modules["core.playblast.tempdir"]` point at the same module, so subpath
# identity (`from pipe.playblast.tempdir import X is core.playblast.tempdir.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.tempdir import *  # noqa: E402, F401, F403
