"""Compatibility shim — real implementation lives in `dcc.maya.util.scripts.Assign_FX_Material`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.scripts.Assign_FX_Material as _real

_sys.modules[__name__] = _real

from dcc.maya.util.scripts.Assign_FX_Material import *  # noqa: E402, F401, F403
