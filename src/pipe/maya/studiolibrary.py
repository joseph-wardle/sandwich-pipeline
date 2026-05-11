"""Compatibility shim — real implementation lives in `dcc.maya.util.studiolibrary`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.studiolibrary as _real

_sys.modules[__name__] = _real

from dcc.maya.util.studiolibrary import *  # noqa: E402, F401, F403
