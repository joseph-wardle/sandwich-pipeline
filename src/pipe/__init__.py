"""Top-level pipe package with lazy submodule loading.

Keep imports lightweight so utility entrypoints (such as telemetry docs
generation) do not require DCC/runtime-only dependencies.
"""

from __future__ import annotations

import importlib as _importlib
import logging as _l
from os import environ as _e
from os import getenv as _getenv
from types import ModuleType as _ModuleType

_BASE_SUBMODULES = [
    "db",
    "environment",
    "glui",
    "shot",
    "struct",
    "telemetry",
    "texconverter",
    "util",
    "versioning",
]
_DCC_SUBMODULES = {"houdini", "maya", "blender", "substance_painter"}

_dcc = _getenv("DCC", "")

__all__ = list(_BASE_SUBMODULES)
if _dcc in _DCC_SUBMODULES:
    __all__.append(_dcc)


def __getattr__(name: str) -> _ModuleType:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = _importlib.import_module(f".{name}", __name__)
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


_l.basicConfig(
    level=int(_e.get("PIPE_LOG_LEVEL") or 0),
    format="%(asctime)s %(processName)s(%(process)s) %(threadName)s [%(name)s(%(lineno)s)] [%(levelname)s] %(message)s",
)
