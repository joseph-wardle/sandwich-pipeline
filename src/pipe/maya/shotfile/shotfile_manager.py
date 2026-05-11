"""Compatibility shim — real implementation lives in `dcc.maya.shotfile.shotfile_manager`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.shotfile.shotfile_manager as _real

_sys.modules[__name__] = _real

from dcc.maya.shotfile.shotfile_manager import *  # noqa: E402, F401, F403
