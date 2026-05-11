"""Internal: turn record() entity kwargs into a {scope_dim: value} dict.

Reads `.code` from ShotGrid entities, strips strings, and drops empties.
Call sites pass entity kwargs straight to `record()`
"""

from __future__ import annotations


def _build_scope_dict(
    *,
    show: object | None = None,
    sequence: object | None = None,
    shot: object | None = None,
    asset: object | None = None,
    department: object | None = None,
) -> dict[str, str]:
    """Build the scope dict that `record()` attaches to an emitted event."""

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
    """Bully a value into a clean string, or None if unusable.

    Strings are stripped; ShotGrid entities yield their `.code`; anything
    else returns None.
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


__all__ = ["_build_scope_dict"]
