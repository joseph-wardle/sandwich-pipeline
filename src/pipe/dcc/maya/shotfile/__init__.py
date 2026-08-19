"""Maya shot files: the anim/RLO managers, the USD stage scaffold, and timelines."""

from __future__ import annotations

import importlib

_MANAGERS = {"MAnimShotFileManager": "anim", "MRLOShotFileManager": "rlo"}

__all__ = [*_MANAGERS]


def __getattr__(name: str) -> type:
    if name in _MANAGERS:
        return getattr(importlib.import_module(f".{_MANAGERS[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
