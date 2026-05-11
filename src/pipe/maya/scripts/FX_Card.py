"""Compatibility shim — real implementation lives in `dcc.maya.util.scripts.FX_Card`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.scripts.FX_Card as _real

_sys.modules[__name__] = _real

from dcc.maya.util.scripts.FX_Card import *  # noqa: E402, F401, F403
