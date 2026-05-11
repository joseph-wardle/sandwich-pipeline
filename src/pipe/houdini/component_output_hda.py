"""Compatibility shim — real implementation lives in `dcc.houdini.publish.component_output_hda`."""

from __future__ import annotations

import sys as _sys

import dcc.houdini.publish.component_output_hda as _real

_sys.modules[__name__] = _real

from dcc.houdini.publish.component_output_hda import *  # noqa: E402, F401, F403
