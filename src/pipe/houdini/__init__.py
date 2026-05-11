"""Compatibility shim — real implementation lives in `dcc.houdini`.

Phase 4 of the structural refactor moved `pipe.houdini` to `dcc.houdini`. The shim
keeps its own module identity (does *not* replace itself in `sys.modules`)
so per-submodule shim files alongside it can run and alias their canonical
`dcc.houdini.*` modules into `sys.modules`. Top-level public names re-export
from the canonical package; chained attribute access (`pipe.houdini.<sub>`)
is handled lazily by `__getattr__` and consults `_RENAMES` for paths whose
move was not 1:1.
"""

from __future__ import annotations

import importlib as _importlib
from types import ModuleType as _ModuleType

from dcc.houdini import *  # noqa: F401, F403

_RENAMES = {
    "local": "dcc.houdini.runtime",
    "reload": "dcc.houdini.util.reload",
    "publish": "dcc.houdini.publish.main",
    "publish_hooks": "dcc.houdini.publish.hooks",
    "assetbuilder": "dcc.houdini.publish.assetbuilder",
    "nodelayouts": "dcc.houdini.publish.nodelayouts",
    "component_output_hda": "dcc.houdini.publish.component_output_hda",
    "shading": "dcc.houdini.shading.main",
    "variants": "dcc.houdini.shading.variants",
    "animpostprocess": "dcc.houdini.shot.animpostprocess",
}


def __getattr__(name: str) -> _ModuleType:
    target = _RENAMES.get(name, f"dcc.houdini.{name}")
    try:
        return _importlib.import_module(target)
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
