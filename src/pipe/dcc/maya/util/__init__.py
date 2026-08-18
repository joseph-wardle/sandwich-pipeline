"""Maya utility helpers (option vars, picker, scale ref, space switch, etc.)."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = [
    "on_open",
    "optionvar",
    "picker",
    "random_color",
    "reload",
    "scale_reference",
    "selection",
    "space_switch",
    "studiolibrary",
    "time",
    "turnaround",
]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
