"""Compatibility shim — real implementation lives in `dcc.houdini.hipfile.shot`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.hipfile.shot as _real

_sys.modules[__name__] = _real

from dcc.houdini.hipfile.shot import *  # noqa: E402, F401, F403
