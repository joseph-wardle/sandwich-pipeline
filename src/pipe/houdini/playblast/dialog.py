"""Compatibility shim — real implementation lives in `dcc.houdini.playblast.dialog`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.playblast.dialog as _real

_sys.modules[__name__] = _real

from dcc.houdini.playblast.dialog import *  # noqa: E402, F401, F403
