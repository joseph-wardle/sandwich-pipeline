"""Compatibility shim — real implementation lives in `dcc.houdini.playblast`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.playblast as _real

_sys.modules[__name__] = _real

from dcc.houdini.playblast import *  # noqa: E402, F401, F403
