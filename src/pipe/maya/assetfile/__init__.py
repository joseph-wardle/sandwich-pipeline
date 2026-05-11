"""Compatibility shim — real implementation lives in `dcc.maya.assetfile`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.assetfile as _real

_sys.modules[__name__] = _real

from dcc.maya.assetfile import *  # noqa: E402, F401, F403
