"""Compatibility shim — real implementation lives in `dcc.maya`.

Phase 4 of the structural refactor moved `pipe.maya` to `dcc.maya`. The shim
keeps its own module identity (does *not* replace itself in `sys.modules`)
so per-submodule shim files alongside it can run and alias their canonical
`dcc.maya.*` modules into `sys.modules`. Top-level public names re-export
from the canonical package; chained attribute access (`pipe.maya.<sub>`)
is handled lazily by `__getattr__` and consults `_RENAMES` for paths whose
move was not 1:1.
"""

from __future__ import annotations

import importlib as _importlib
from types import ModuleType as _ModuleType

from dcc.maya import *  # noqa: F401, F403

_RENAMES = {
    "local": "dcc.maya.runtime",
    "optionvar": "dcc.maya.util.optionvar",
    "util": "dcc.maya.util.util",
    "reload": "dcc.maya.util.reload",
    "picker": "dcc.maya.util.picker",
    "studiolibrary": "dcc.maya.util.studiolibrary",
    "scale_reference": "dcc.maya.util.scale_reference",
    "space_switch": "dcc.maya.util.space_switch",
    "turnaround": "dcc.maya.util.turnaround",
}


def __getattr__(name: str) -> _ModuleType:
    target = _RENAMES.get(name, f"dcc.maya.{name}")
    try:
        return _importlib.import_module(target)
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
