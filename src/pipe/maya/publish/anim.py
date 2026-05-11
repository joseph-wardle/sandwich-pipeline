"""Compatibility shim — real implementation lives in `dcc.maya.publish.anim`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.publish.anim as _real

_sys.modules[__name__] = _real

from dcc.maya.publish.anim import *  # noqa: E402, F401, F403
