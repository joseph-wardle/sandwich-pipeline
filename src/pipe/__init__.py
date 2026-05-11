"""Compatibility shim package — pipeline domain modules now live under `core.*`.

Phase 3 of the structural refactor moved nine cross-DCC domain packages
(`asset`, `environment`, `glui`, `playblast`, `shot`, `shotgrid`, `struct`,
`telemetry`, `versioning`), the shared `util/` package, and `texconverter.py`
out of `pipe/` and into `core/`. The DCC-specific subpackages `pipe.maya`,
`pipe.houdini`, `pipe.blender`, and `pipe.substance_painter` continue to
live here through Phase 3; Phase 4 of the refactor moves them under
`dcc.<name>` and migrates the DCC-context gating below into `dcc/__init__.py`.

Each `pipe/<moved>[/<sub>].py` shim under this package re-binds the
corresponding `core.*` module via `sys.modules` so identity checks
(`isinstance`, `is`) hold across the legacy `pipe.*` and canonical `core.*`
paths. Phase 5 of the refactor rewrites every caller to import from `core.*`
directly and deletes the shims.
"""

from __future__ import annotations

import importlib as _importlib
import logging as _logging
from os import environ as _environ
from os import getenv as _getenv
from types import ModuleType as _ModuleType

# Which `pipe.<dcc>` submodule is reachable depends on the current DCC
# context. Outside any DCC (headless / farm), none of them resolve; inside
# Maya, only `pipe.maya` does; etc. This stops outer-venv tooling from
# accidentally loading a DCC-API module that would crash on import.
_DCC_SUBMODULES = frozenset({"houdini", "maya", "blender", "substance_painter"})
_dcc = _getenv("DCC", "")

__all__: list[str] = []
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


_logging.basicConfig(
    level=int(_environ.get("PIPE_LOG_LEVEL") or 0),
    format="%(asctime)s %(processName)s(%(process)s) %(threadName)s [%(name)s(%(lineno)s)] [%(levelname)s] %(message)s",
)
