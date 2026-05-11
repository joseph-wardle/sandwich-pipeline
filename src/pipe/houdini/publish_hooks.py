"""Compatibility shim — real implementation lives in `dcc.houdini.publish.hooks`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.publish.hooks as _real

_sys.modules[__name__] = _real

from dcc.houdini.publish.hooks import *  # noqa: E402, F401, F403
