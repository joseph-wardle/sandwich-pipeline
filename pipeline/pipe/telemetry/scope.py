"""Internal: turn record() entity kwargs into a {scope_dim: value} dict.

`record()` accepts the five known scope dimensions as named kwargs (one
each for show, sequence, shot, asset, department). This module's job is to
coerce those kwargs into the flat string dict that the JSONL writer and
the ingester expect — reading `.code` from ShotGrid-style entities,
stripping strings, and dropping anything that resolves to empty.

The word "scope" stays inside `pipe.telemetry/`. Call sites pass entity
kwargs straight to `record()` and never see this module.
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
    """Coerce a candidate scope value to a clean string, or None if unusable.

    Strings are stripped. Objects with a `code` attribute (every ShotGrid
    entity in this repo) read that. Anything else is rejected.
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
