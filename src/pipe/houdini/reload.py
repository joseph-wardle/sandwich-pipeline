"""Compatibility shim — real implementation lives in `dcc.houdini.util.reload`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.util.reload as _real

_sys.modules[__name__] = _real

from dcc.houdini.util.reload import *  # noqa: E402, F401, F403
