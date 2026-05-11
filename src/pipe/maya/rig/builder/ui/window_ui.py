"""Compatibility shim — real implementation lives in `dcc.maya.rig.builder.ui.window_ui`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.rig.builder.ui.window_ui as _real

_sys.modules[__name__] = _real

from dcc.maya.rig.builder.ui.window_ui import *  # noqa: E402, F401, F403
