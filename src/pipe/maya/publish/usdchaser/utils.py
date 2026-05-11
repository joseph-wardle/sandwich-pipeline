"""Compatibility shim — real implementation lives in `dcc.maya.publish.usdchaser.utils`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.publish.usdchaser.utils as _real

_sys.modules[__name__] = _real

from dcc.maya.publish.usdchaser.utils import *  # noqa: E402, F401, F403
