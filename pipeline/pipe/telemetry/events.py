"""Registry-backed telemetry event constants.

Event names are declared once in ``registry.py``. This module derives stable
constant names from that registry so instrumentation can import constants
instead of repeating string literals at callsites.
"""

from __future__ import annotations

from typing import Final

from .registry import EVENT_TYPES


def _constant_name(event_type: str) -> str:
    return "EVENT_" + event_type.replace(".", "_").upper()


_CONSTANT_ITEMS = tuple(
    (_constant_name(event_type), event_type) for event_type in EVENT_TYPES
)

_seen_constant_names: set[str] = set()
for constant_name, event_type in _CONSTANT_ITEMS:
    if constant_name in _seen_constant_names:
        raise ValueError(
            f"Duplicate telemetry event constant name '{constant_name}' "
            f"derived from event type '{event_type}'"
        )
    _seen_constant_names.add(constant_name)


EVENT_CONSTANTS: Final[dict[str, str]] = dict(_CONSTANT_ITEMS)
globals().update(EVENT_CONSTANTS)


def event_constant_name(event_type: str) -> str:
    """Return the generated constant name for a known telemetry event type."""

    constant_name = _constant_name(event_type)
    if constant_name not in EVENT_CONSTANTS:
        raise KeyError(f"Unknown telemetry event type '{event_type}'")
    return constant_name


def __getattr__(name: str) -> str:
    """Resolve generated event constants for static analyzers and runtime access."""
    try:
        return EVENT_CONSTANTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc


__all__ = ["EVENT_CONSTANTS", "event_constant_name", *sorted(EVENT_CONSTANTS.keys())]
