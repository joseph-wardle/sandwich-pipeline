"""Compatibility shim — real implementation lives in `dcc.maya.util.picker`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.picker as _real

_sys.modules[__name__] = _real

from dcc.maya.util.picker import *  # noqa: E402, F401, F403
