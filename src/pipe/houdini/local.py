"""Compatibility shim — real implementation lives in `dcc.houdini.runtime`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.runtime as _real

_sys.modules[__name__] = _real

from dcc.houdini.runtime import *  # noqa: E402, F401, F403
