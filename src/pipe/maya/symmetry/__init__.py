"""Compatibility shim — real implementation lives in `dcc.maya.symmetry`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.symmetry as _real

_sys.modules[__name__] = _real

from dcc.maya.symmetry import *  # noqa: E402, F401, F403
