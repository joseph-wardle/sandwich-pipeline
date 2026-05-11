"""Compatibility shim — real implementation lives in `dcc.maya.shotfile.timeline`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.shotfile.timeline as _real

_sys.modules[__name__] = _real

from dcc.maya.shotfile.timeline import *  # noqa: E402, F401, F403
