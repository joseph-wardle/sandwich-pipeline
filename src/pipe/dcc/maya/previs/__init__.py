"""Maya previs sequencer: per-file state, the dockable panel, and shot playblasts."""

from __future__ import annotations

import importlib

# Re-exported lazily: the scene's open hook is written as
# `from <package> import <class>`, so the name has to resolve on the package —
# but importing the file manager eagerly would drag Qt and USD into every
# module that only wants `previs.state`.
_MANAGERS = {"MPrevisFileManager": "file_manager"}

__all__ = [*_MANAGERS]


def __getattr__(name: str) -> type:
    if name in _MANAGERS:
        return getattr(importlib.import_module(f".{_MANAGERS[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
