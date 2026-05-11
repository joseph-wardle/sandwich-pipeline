"""Compatibility shim — real implementation lives in `dcc.houdini.publish.main`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.publish.main as _real

_sys.modules[__name__] = _real

from dcc.houdini.publish.main import *  # noqa: E402, F401, F403
