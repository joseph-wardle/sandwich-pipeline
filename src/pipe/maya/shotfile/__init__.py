"""Compatibility shim — real implementation lives in `dcc.maya.shotfile`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.shotfile as _real

_sys.modules[__name__] = _real

from dcc.maya.shotfile import *  # noqa: E402, F401, F403
