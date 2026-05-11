"""Compatibility shim — real implementation lives in `dcc.maya.util.scripts.Pointlight_Rig`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.scripts.Pointlight_Rig as _real

_sys.modules[__name__] = _real

from dcc.maya.util.scripts.Pointlight_Rig import *  # noqa: E402, F401, F403
