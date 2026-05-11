"""Compatibility shim — real implementation lives in `dcc.maya.layout`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.layout as _real

_sys.modules[__name__] = _real

from dcc.maya.layout import *  # noqa: E402, F401, F403
