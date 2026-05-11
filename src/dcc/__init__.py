"""Per-DCC integration packages with `$DCC`-context gating.

Each `dcc.<name>` subpackage holds the entire integration for one DCC
(launcher, runtime contracts, site/, util/, command/, feature folders,
third_party/). The runtime module (`dcc.<name>.runtime`) is the only piece
that imports the DCC API at module level; the package `__init__.py`
re-exports `<Dcc>Launcher` only (NOT `<Dcc>Runtime`), so this package
stays import-safe in the outer launcher venv before any DCC subprocess
exists. Feature code that runs inside the DCC reaches the runtime via
`from dcc.<name>.runtime import ...` directly.

Which `dcc.<name>` resolves depends on the current DCC context: inside
Maya only `dcc.maya` is reachable; inside Houdini only `dcc.houdini`;
outside any DCC (headless / farm / pipe-only Python), none. This
prevents outer-venv tooling from accidentally loading a runtime module
that would crash on import because its DCC API is unavailable.
"""

from __future__ import annotations

import importlib as _importlib
from os import getenv as _getenv
from types import ModuleType as _ModuleType

_DCC_PACKAGES = frozenset(
    {
        "blender",
        "houdini",
        "maya",
        "nuke",
        "substance_designer",
        "substance_painter",
    }
)
_dcc = _getenv("DCC", "")

__all__: list[str] = []
if _dcc in _DCC_PACKAGES:
    __all__.append(_dcc)


def __getattr__(name: str) -> _ModuleType:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = _importlib.import_module(f".{name}", __name__)
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
