"""Compatibility shim — real implementation lives in `dcc.maya.util.reload`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.reload as _real

_sys.modules[__name__] = _real

from dcc.maya.util.reload import *  # noqa: E402, F401, F403
