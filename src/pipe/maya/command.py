"""Compatibility shim — real implementation lives in `dcc.maya.command`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.command as _real

_sys.modules[__name__] = _real

from dcc.maya.command import *  # noqa: E402, F401, F403
