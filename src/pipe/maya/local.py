"""Compatibility shim — real implementation lives in `dcc.maya.runtime`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.runtime as _real

_sys.modules[__name__] = _real

from dcc.maya.runtime import *  # noqa: E402, F401, F403
