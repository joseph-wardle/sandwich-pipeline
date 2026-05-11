"""Compatibility shim — real implementation lives in `dcc.substance_painter`.

Phase 4 of the structural refactor moved `pipe.substance_painter` to `dcc.substance_painter`. The shim
keeps its own module identity (does *not* replace itself in `sys.modules`)
so per-submodule shim files alongside it can run and alias their canonical
`dcc.substance_painter.*` modules into `sys.modules`. Top-level public names re-export
from the canonical package; chained attribute access (`pipe.substance_painter.<sub>`)
is handled lazily by `__getattr__` and consults `_RENAMES` for paths whose
move was not 1:1.
"""

from __future__ import annotations

import importlib as _importlib
from types import ModuleType as _ModuleType

from dcc.substance_painter import *  # noqa: F401, F403

_RENAMES = {
    "local": "dcc.substance_painter.runtime",
    "util": "dcc.substance_painter.util.util",
    "metadata": "dcc.substance_painter.util.metadata",
    "progress": "dcc.substance_painter.util.progress",
    "reload": "dcc.substance_painter.util.reload",
    "export": "dcc.substance_painter.export.export",
    "export_config": "dcc.substance_painter.export.config",
    "export_material_info": "dcc.substance_painter.export.material_info",
    "export_results": "dcc.substance_painter.export.results",
    "export_types": "dcc.substance_painter.export.types",
    "houdini": "dcc.substance_painter.houdini_bridge",
}


def __getattr__(name: str) -> _ModuleType:
    target = _RENAMES.get(name, f"dcc.substance_painter.{name}")
    try:
        return _importlib.import_module(target)
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
