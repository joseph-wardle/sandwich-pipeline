"""Maya shot files: the anim/RLO managers, the USD stage scaffold, and timelines."""

from __future__ import annotations

import importlib

# Everything in-tree imports the submodule it wants; these two names exist for the
# `shelf_SKD_RLO` / `shelf_SKD_Animation` buttons, which import them off the package.
_MANAGERS = {"MAnimShotFileManager": "anim", "MRLOShotFileManager": "rlo"}

__all__ = [*_MANAGERS]


def __getattr__(name: str) -> type:
    if name in _MANAGERS:
        return getattr(importlib.import_module(f".{_MANAGERS[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
