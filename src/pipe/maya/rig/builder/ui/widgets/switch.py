"""Compatibility shim — real implementation lives in `dcc.maya.rig.builder.ui.widgets.switch`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.rig.builder.ui.widgets.switch as _real

_sys.modules[__name__] = _real

from dcc.maya.rig.builder.ui.widgets.switch import *  # noqa: E402, F401, F403
