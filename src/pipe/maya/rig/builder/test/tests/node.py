"""Compatibility shim — real implementation lives in `dcc.maya.rig.builder.test.tests.node`."""

from __future__ import annotations

import sys as _sys

import dcc.maya.rig.builder.test.tests.node as _real

_sys.modules[__name__] = _real

from dcc.maya.rig.builder.test.tests.node import *  # noqa: E402, F401, F403
