"""Exceptions raised by the ShotGrid client.

Callers import these directly (`from core.shotgrid import ShotGridNotFound`)
and catch them by type.  The string forms are production-facing: they describe
the problem in the pipeline's own terms and never leak `shotgun_api3` internals.

When a ShotGrid write fails, the underlying `shotgun_api3.Fault` is wrapped
in `ShotGridWriteError` and attached to `__cause__` so developers
still see the original traceback while artists see only a clean message.
"""

from __future__ import annotations

from dataclasses import dataclass


class ShotGridError(Exception):
    """Base class for every ShotGrid-related error raised by `core.shotgrid`.

    Catch this to handle any ShotGrid failure uniformly.  Catch a subclass to
    react to a specific failure mode.
    """


@dataclass(frozen=True)
class ShotGridNotFound(ShotGridError):
    """Raised when a `get_*` lookup finds no matching entity.

    The caller asked for a unique entity by a unique selector and ShotGrid
    returned nothing.  `find_*` methods never raise this — they return an
    empty list.

    Attributes:
        entity_type: Human-readable entity class name (`"Asset"`, `"Shot"`, ...).
        selector: Which field was used to look up (`"id"`, `"name"`, ...).
        value: The value the caller passed for that selector.  Safe to print.
    """

    entity_type: str
    selector: str
    value: object

    def __str__(self) -> str:
        return f"No {self.entity_type} found where {self.selector}={self.value!r}."


@dataclass(frozen=True)
class ShotGridAmbiguous(ShotGridError):
    """Raised when a `get_*` lookup matches more than one entity.

    Only possible when the selector is not unique in ShotGrid (typically a
    display name collision).  The caller should re-issue the query using `id`.

    Attributes:
        entity_type: Human-readable entity class name.
        selector: Which field was used to look up.
        value: The value the caller passed for that selector.
        matching_ids: All ShotGrid ids that matched.  Useful for disambiguation.
    """

    entity_type: str
    selector: str
    value: object
    matching_ids: list[int]

    def __str__(self) -> str:
        return (
            f"Multiple {self.entity_type}s where {self.selector}={self.value!r}: "
            f"ids={self.matching_ids}. Disambiguate by id."
        )


@dataclass(frozen=True)
class ShotGridWriteError(ShotGridError):
    """Raised when a ShotGrid write (create / update / upload) fails.

    The original `shotgun_api3.Fault` (or other underlying exception) is set
    on `__cause__` so the developer traceback is preserved.  The string form
    is artist-safe and does not include ``shotgun_api3`` jargon.

    Attributes:
        entity_type: Entity class being written (`"Asset"`, `"Version"`, ...).
        entity_id: ShotGrid id if the entity already existed, else `None`.
        field: Field name the write targeted, if the failure is field-scoped.
        cause: The underlying exception.  Mirrored onto `__cause__` when raised.
    """

    entity_type: str
    entity_id: int | None
    field: str | None
    cause: BaseException | None

    def __str__(self) -> str:
        target = self.entity_type
        if self.entity_id is not None:
            target = f"{target} id={self.entity_id}"
        if self.field is not None:
            target = f"{target} field {self.field!r}"
        return f"ShotGrid write failed for {target}."
