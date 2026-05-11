"""Compatibility shim — real implementation lives in `dcc.blender.publish`."""

from __future__ import annotations

import sys as _sys

import dcc.blender.publish as _real

_sys.modules[__name__] = _real

from dcc.blender.publish import *  # noqa: E402, F401, F403
