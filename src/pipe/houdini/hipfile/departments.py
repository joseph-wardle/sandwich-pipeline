"""Compatibility shim — real implementation lives in `dcc.houdini.hipfile.departments`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.hipfile.departments as _real

_sys.modules[__name__] = _real

from dcc.houdini.hipfile.departments import *  # noqa: E402, F401, F403
