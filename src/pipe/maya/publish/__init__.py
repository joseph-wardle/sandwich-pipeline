"""Compatibility shim — real implementation lives in `dcc.maya.publish`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.publish as _real

_sys.modules[__name__] = _real

from dcc.maya.publish import *  # noqa: E402, F401, F403
