"""Pipeline telemetry — record what tools did, how long it took, and what failed.

Wrap a workflow step with the `record()` context manager:

    from pipe import telemetry

    with telemetry.record(
        telemetry.EVENT_PUBLISH_USD,
        payload={"kind": "asset", "publish_path": str(path)},
        asset=asset,
    ) as telemetry_event:
        do_the_publish()

The event context manager emits exactly one terminal event on exit
(`success` with duration, or `error` with `error_code` from the exception).
It never suppresses exceptions.

The `payload` kwarg is the dict of event-specific facts — what happened.
The five entity kwargs (`show`, `sequence`, `shot`, `asset`, `department`)
name the production entities this event is *for*; each accepts a ShotGrid
entity object with a `.code` attribute or a plain string. Pass only the
dimensions that apply to the event.

Failure classification: `record()` reads `exc.error_code` from any exception
that escapes a wrapped block. Workflow modules define their own typed
exceptions next to the raise sites (e.g. `PlayblastError`,
`USDExportError`) and set `error_code` as a class attribute. Anything
without the attribute falls through to `error_code = "UNKNOWN"`. Call
sites can also override with `telemetry_event.fail(code, message)` inside
the block — typically when the work returns a structured result instead
of raising.

Where to find what:

- ``events.py``  — the tool event types this pipeline emits, plus payload contracts
- ``scope.py``   — internal coercion of entity kwargs into the scope dict
- ``emit.py``    — implementation of record() / Event and the lower-level emit()
- ``spool.py``   — JSONL writer to the shared production spool
- ``config.py``  — env-var driven settings (PIPE_TELEMETRY_*)
"""

from __future__ import annotations

from .emit import (
    Event,
    _running_under_parent_event,
    emit,
    record,
)
from .events import (
    EVENT_BUILD_HOUDINI_COMPONENT,
    EVENT_DCC_LAUNCH,
    EVENT_DEFINITIONS,
    EVENT_PLAYBLAST_CREATE,
    EVENT_PUBLISH_USD,
    EVENT_TEXTURE_CONVERT_TEX,
    EVENT_TEXTURE_EXPORT_SUBSTANCE,
    EVENTS_BY_TYPE,
    STATUS_ERROR,
    STATUS_SUCCESS,
    EventDefinition,
    Status,
    get_event_definition,
)

__all__ = [
    # Public API: workflow CM and bare emit
    "record",
    "Event",
    "emit",
    # Subprocess detection (used at DCC entry points)
    "_running_under_parent_event",
    # Event types
    "EVENT_DCC_LAUNCH",
    "EVENT_PUBLISH_USD",
    "EVENT_BUILD_HOUDINI_COMPONENT",
    "EVENT_TEXTURE_EXPORT_SUBSTANCE",
    "EVENT_TEXTURE_CONVERT_TEX",
    "EVENT_PLAYBLAST_CREATE",
    # Status values
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "Status",
    # Registry inspection
    "EventDefinition",
    "EVENT_DEFINITIONS",
    "EVENTS_BY_TYPE",
    "get_event_definition",
]
