"""Compatibility shim — real implementation lives in `dcc.houdini.hipfile.filemanager`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.hipfile.filemanager as _real

_sys.modules[__name__] = _real

from dcc.houdini.hipfile.filemanager import *  # noqa: E402, F401, F403
