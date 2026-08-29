"""Bucket entities into the groups the pickers render as collapsible sections."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pipe.core.shotgrid.entities import Asset, Environment, Shot

OTHER_GROUP = "Other"

_SubdirectoryEntity = Asset | Environment


def group_assets_by_subdirectory(
    assets: Iterable[_SubdirectoryEntity],
    *,
    key: Callable[[_SubdirectoryEntity], str] = lambda asset: asset.display_name,
) -> dict[str, list[str]] | list[str]:
    """Group assets (or environments) by their ShotGrid subdirectory."""
    return _bucket((asset.as_fetched("subdirectory"), key(asset)) for asset in assets)


def group_shots_by_sequence(shots: Iterable[Shot]) -> dict[str, list[str]] | list[str]:
    """Group shots by the sequence that owns them."""
    return _bucket(
        (
            sequence.code if (sequence := shot.as_fetched("sequence")) else None,
            shot.code,
        )
        for shot in shots
        if shot.code
    )


def _bucket(
    pairs: Iterable[tuple[str | None, str]],
) -> dict[str, list[str]] | list[str]:
    """Collect `(group, row)` pairs into sorted groups, `OTHER_GROUP` last."""
    buckets: dict[str, list[str]] = {}
    for group, row in pairs:
        buckets.setdefault(group or OTHER_GROUP, []).append(row)

    if len(buckets) <= 1:
        return sorted(row for rows in buckets.values() for row in rows)

    return {
        group: sorted(buckets[group])
        for group in sorted(buckets, key=lambda g: (g == OTHER_GROUP, g.lower()))
    }
