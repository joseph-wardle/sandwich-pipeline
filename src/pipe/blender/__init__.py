"""Compatibility shim — real implementation lives in `dcc.blender`.

Phase 4 of the structural refactor moved `pipe.blender` to `dcc.blender`. The shim
keeps its own module identity (does *not* replace itself in `sys.modules`)
so per-submodule shim files alongside it can run and alias their canonical
`dcc.blender.*` modules into `sys.modules`. Top-level public names re-export
from the canonical package; chained attribute access (`pipe.blender.<sub>`)
is handled lazily by `__getattr__` and consults `_RENAMES` for paths whose
move was not 1:1.
"""

from __future__ import annotations

import importlib as _importlib
from types import ModuleType as _ModuleType

from dcc.blender import *  # noqa: F401, F403

_RENAMES = {"register": "dcc.blender.util.register"}


def __getattr__(name: str) -> _ModuleType:
    target = _RENAMES.get(name, f"dcc.blender.{name}")
    try:
        return _importlib.import_module(target)
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
