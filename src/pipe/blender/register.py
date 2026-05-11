"""Compatibility shim — real implementation lives in `dcc.blender.util.register`."""

from __future__ import annotations

import sys as _sys

import dcc.blender.util.register as _real

_sys.modules[__name__] = _real

from dcc.blender.util.register import *  # noqa: E402, F401, F403
