"""Compatibility shim — real implementation lives in `dcc.substance_painter.channels`."""

from __future__ import annotations

import sys as _sys

import dcc.substance_painter.channels as _real

_sys.modules[__name__] = _real

from dcc.substance_painter.channels import *  # noqa: E402, F401, F403
