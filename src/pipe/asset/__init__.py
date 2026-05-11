"""Compatibility shim — real implementation lives in `core.asset`.

Phase 3 of the structural refactor moved this package out of `pipe/` and
into `core/`. Existing `from pipe.asset import X` and
`from pipe.asset.<sub> import X` imports continue to resolve here through
Phase 5, which deletes the shim and rewrites callers to use `core.asset`
directly.

The shim package keeps its own module identity (it does *not* replace itself
in `sys.modules`) so per-submodule shim files at `pipe/asset/<sub>.py`
still execute and can alias their canonical `core.asset.<sub>` module
into `sys.modules`. Top-level attributes are re-exported below; chained
attribute access (`pipe.asset.<sub>`) is handled lazily by `__getattr__`
so heavy submodules don't load unnecessarily.
"""

from __future__ import annotations

import importlib as _importlib
from types import ModuleType as _ModuleType

from core.asset import *  # noqa: F401, F403


def __getattr__(name: str) -> _ModuleType:
    """Lazily resolve `pipe.asset.<sub>` to the canonical `core.asset.<sub>`."""
    try:
        return _importlib.import_module(f"core.asset.{name}")
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
