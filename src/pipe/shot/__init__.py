"""Compatibility shim — real implementation lives in `core.shot`.

Phase 3 of the structural refactor moved this package out of `pipe/` and
into `core/`. Existing `from pipe.shot import X` and
`from pipe.shot.<sub> import X` imports continue to resolve here through
Phase 5, which deletes the shim and rewrites callers to use `core.shot`
directly.

The shim package keeps its own module identity (it does *not* replace itself
in `sys.modules`) so per-submodule shim files at `pipe/shot/<sub>.py`
still execute and can alias their canonical `core.shot.<sub>` module
into `sys.modules`. Top-level attributes are re-exported below; chained
attribute access (`pipe.shot.<sub>`) is handled lazily by `__getattr__`
so heavy submodules don't load unnecessarily.
"""

from __future__ import annotations

import importlib as _importlib
from types import ModuleType as _ModuleType

from core.shot import *  # noqa: F401, F403


def __getattr__(name: str) -> _ModuleType:
    """Lazily resolve `pipe.shot.<sub>` to the canonical `core.shot.<sub>`."""
    try:
        return _importlib.import_module(f"core.shot.{name}")
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
