"""Compatibility shim — real implementation lives in `dcc.maya.rig.utils`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.rig.utils as _real

_sys.modules[__name__] = _real

from dcc.maya.rig.utils import *  # noqa: E402, F401, F403
