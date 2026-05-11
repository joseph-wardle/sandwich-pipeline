"""Compatibility shim — real implementation lives in `dcc.houdini.playblast.hud`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.playblast.hud as _real

_sys.modules[__name__] = _real

from dcc.houdini.playblast.hud import *  # noqa: E402, F401, F403
