"""Build a `{scope_dim: value}` dict for one telemetry event.

Each call site that wraps a workflow in `action()` declares which scope
dimensions apply to it by passing entities (or strings) to `build_scope`:

```python
from pipe.telemetry import action, build_scope

with action(
    "publish.usd",
    payload={"kind": "asset", "publish_path": str(path)},
    scope=build_scope(asset=self._entity, shot=self._shot),
):
    do_the_publish()
```

For object arguments (Asset, Shot, Sequence, Environment), the canonical
`code` attribute is read. Strings are accepted as-is and stripped. None
values are skipped. The result is the dict consumed by
`pipe.telemetry.action(scope=...)` and persisted as `scope_<dim>` columns
by the ingester.
"""

from __future__ import annotations


def build_scope(
    *,
    show: object | None = None,
    sequence: object | None = None,
    shot: object | None = None,
    asset: object | None = None,
    department: object | None = None,
) -> dict[str, str]:
    """Build a scope dict for one telemetry event."""

    out: dict[str, str] = {}
    for dim, value in (
        ("show", show),
        ("sequence", sequence),
        ("shot", shot),
        ("asset", asset),
        ("department", department),
    ):
        resolved = _resolve_scope_value(value)
        if resolved is not None:
            out[dim] = resolved
    return out


def _resolve_scope_value(value: object | None) -> str | None:
    """Coerce a candidate scope value to a clean string, or None if unusable.

    Strings are stripped. Objects with a `code` attribute (every ShotGrid
    entity in this repo) read that. Anything else is rejected
    """

    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    code = getattr(value, "code", None)
    if isinstance(code, str):
        stripped = code.strip()
        return stripped or None
    return None


__all__ = ["build_scope"]
