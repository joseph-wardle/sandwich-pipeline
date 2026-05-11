"""Pipeline telemetry — record what tools did, how long it took, and what failed.

Wrap a workflow step with `record()`:

    from core import telemetry

    with telemetry.record(
        telemetry.EVENT_PUBLISH_USD,
        payload={"kind": "asset", "publish_path": str(path)},
        asset=asset,
    ) as telemetry_event:
        do_the_publish()

One event lands in the spool when the block exits, either `success` with a
duration on clean exit, or `error` carrying the exception's `error_code`
on failure. The block never swallows the exception.
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
