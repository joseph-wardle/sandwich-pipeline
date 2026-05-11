"""Compatibility shim — real implementation lives in `dcc.maya.rig`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.rig as _real

_sys.modules[__name__] = _real

from dcc.maya.rig import *  # noqa: E402, F401, F403
