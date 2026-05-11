"""Compatibility shim — real implementation lives in `core.texconverter`."""

from __future__ import annotations

import sys as _sys

import core.texconverter as _real

_sys.modules[__name__] = _real

from core.texconverter import *  # noqa: E402, F401, F403
