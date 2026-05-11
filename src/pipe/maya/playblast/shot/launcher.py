"""Compatibility shim — real implementation lives in `dcc.maya.playblast.shot.launcher`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.playblast.shot.launcher as _real

_sys.modules[__name__] = _real

from dcc.maya.playblast.shot.launcher import *  # noqa: E402, F401, F403
