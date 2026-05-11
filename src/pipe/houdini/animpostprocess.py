"""Compatibility shim — real implementation lives in `dcc.houdini.shot.animpostprocess`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.shot.animpostprocess as _real

_sys.modules[__name__] = _real

from dcc.houdini.shot.animpostprocess import *  # noqa: E402, F401, F403
