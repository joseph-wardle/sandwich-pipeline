"""Compatibility shim — real implementation lives in `dcc.maya.layout.publish`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.layout.publish as _real

_sys.modules[__name__] = _real

from dcc.maya.layout.publish import *  # noqa: E402, F401, F403
