"""Compatibility shim — real implementation lives in `dcc.houdini.playblast.config`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.playblast.config as _real

_sys.modules[__name__] = _real

from dcc.houdini.playblast.config import *  # noqa: E402, F401, F403
