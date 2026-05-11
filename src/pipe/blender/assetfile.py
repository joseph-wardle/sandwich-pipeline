"""Compatibility shim — real implementation lives in `dcc.blender.assetfile`."""

from __future__ import annotations

import sys as _sys

import dcc.blender.assetfile as _real

_sys.modules[__name__] = _real

from dcc.blender.assetfile import *  # noqa: E402, F401, F403
