"""Compatibility shim — real implementation lives in `dcc.maya.publish.asset`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.publish.asset as _real

_sys.modules[__name__] = _real

from dcc.maya.publish.asset import *  # noqa: E402, F401, F403
