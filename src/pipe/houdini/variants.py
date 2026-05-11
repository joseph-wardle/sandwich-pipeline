"""Compatibility shim — real implementation lives in `dcc.houdini.shading.variants`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.shading.variants as _real

_sys.modules[__name__] = _real

from dcc.houdini.shading.variants import *  # noqa: E402, F401, F403
