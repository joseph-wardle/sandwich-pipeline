"""Compatibility shim — real implementation lives in `dcc.houdini.publish.assetbuilder`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.publish.assetbuilder as _real

_sys.modules[__name__] = _real

from dcc.houdini.publish.assetbuilder import *  # noqa: E402, F401, F403
