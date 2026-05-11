"""Compatibility shim — real implementation lives in `dcc.maya.layout.maker`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.layout.maker as _real

_sys.modules[__name__] = _real

from dcc.maya.layout.maker import *  # noqa: E402, F401, F403
