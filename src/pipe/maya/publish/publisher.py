"""Compatibility shim — real implementation lives in `dcc.maya.publish.publisher`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.publish.publisher as _real

_sys.modules[__name__] = _real

from dcc.maya.publish.publisher import *  # noqa: E402, F401, F403
