"""Compatibility shim — real implementation lives in `dcc.houdini.shading.main`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.shading.main as _real

_sys.modules[__name__] = _real

from dcc.houdini.shading.main import *  # noqa: E402, F401, F403
