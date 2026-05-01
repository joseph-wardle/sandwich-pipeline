"""Scope extraction — turn entity-shaped objects into the canonical scope dict.

Most call sites have access to entity objects (the active Asset, Shot, Sequence)
already. They pass those objects to `extract_scope`, which reads the canonical
fields without each caller knowing the shape:

```python
from pipe.telemetry import extract_scope

scope = extract_scope(self._entity, self._shot)
# {"shot": "SQ010_010", "asset": "Hero"}
```

Sources can be:
- ScopeContext (returned as-is)
- Mapping (read by canonical key, then by alias)
- Object with attributes like `code`, `name`, `display_name`, `content`, `id`
- Nested objects (for ShotGrid records: `entity.code`)

Later sources override earlier ones, so the most-specific entity goes last.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

SCOPE_FIELDS: Final[tuple[str, ...]] = (
    "show",
    "sequence",
    "shot",
    "asset",
    "department",
    "task",
)

_SCOPE_FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "show": ("show", "show_code", "project", "project_code"),
    "sequence": ("sequence", "sequence_code", "seq"),
    "shot": ("shot", "shot_code", "entity", "entity_code"),
    "asset": ("asset", "asset_code", "asset_name"),
    "department": ("department", "dept", "step"),
    "task": ("task", "task_name", "content"),
}

_NESTED_VALUE_ATTRS: Final[tuple[str, ...]] = (
    "code",
    "name",
    "display_name",
    "content",
    "id",
)


@dataclass(frozen=True)
class ScopeContext:
    """Canonical scope keys captured from an entity context."""

    show: str | None = None
    sequence: str | None = None
    shot: str | None = None
    asset: str | None = None
    department: str | None = None
    task: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            field: value
            for field in SCOPE_FIELDS
            if (value := getattr(self, field)) is not None
        }


def _read_attr_or_key(source: Any, key: str) -> Any:
    """Return the value at `key` from a mapping or object, or None if absent."""

    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _normalize_scope_value(value: Any) -> str | None:
    """Coerce a candidate scope value to a clean string, or None if not usable.

    Falls through nested objects/mappings looking for a `code`/`name`/etc.
    attribute — ShotGrid records often nest the readable identifier this way.
    """

    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, os.PathLike):
        normalized = os.fspath(value).strip()
        return normalized or None

    for nested_attr in _NESTED_VALUE_ATTRS:
        nested = _read_attr_or_key(value, nested_attr)
        normalized = _normalize_scope_value(nested)
        if normalized is not None:
            return normalized
    return None


def _extract_from_source(source: Any) -> dict[str, str]:
    if source is None:
        return {}
    if isinstance(source, ScopeContext):
        return source.as_dict()

    extracted: dict[str, str] = {}
    for field_name in SCOPE_FIELDS:
        for alias in _SCOPE_FIELD_ALIASES[field_name]:
            normalized = _normalize_scope_value(_read_attr_or_key(source, alias))
            if normalized is not None:
                extracted[field_name] = normalized
                break
    return extracted


def extract_scope(*sources: Any) -> dict[str, str]:
    """Merge scope keys from one or more entity sources, last-source-wins."""

    merged: dict[str, str] = {}
    for source in sources:
        merged.update(_extract_from_source(source))
    return merged


__all__ = ["SCOPE_FIELDS", "ScopeContext", "extract_scope"]
