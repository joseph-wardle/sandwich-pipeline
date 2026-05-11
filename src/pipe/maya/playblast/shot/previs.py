"""Compatibility shim — real implementation lives in `dcc.maya.playblast.shot.previs`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.playblast.shot.previs as _real

_sys.modules[__name__] = _real

from dcc.maya.playblast.shot.previs import *  # noqa: E402, F401, F403
