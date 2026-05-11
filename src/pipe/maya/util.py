"""Compatibility shim — real implementation lives in `dcc.maya.util.util`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.util.util as _real

_sys.modules[__name__] = _real

from dcc.maya.util.util import *  # noqa: E402, F401, F403
