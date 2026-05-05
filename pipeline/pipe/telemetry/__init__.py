"""Pipeline telemetry — record what tools did, how long it took, and what failed.

Wrap a workflow step with the `action` context manager:

    from pipe.telemetry import action

    with action("publish.usd", payload={"kind": "asset", "publish_path": str(path)}):
        do_the_publish()

The action context manager emits exactly one terminal event on exit
(`success` with duration, or `error` with `error_code` from the exception).
It never suppresses exceptions.

Where to find what:

- ``events.py``  — the tool event types this pipeline emits, plus payload contracts
- ``errors.py``  — typed exceptions whose ``error_code`` attribute drives error events
- ``scope.py``   — turn entity-shaped objects into a {show, shot, asset, ...} dict
- ``emit.py``    — implementation of action() and the lower-level emit()
- ``spool.py``   — JSONL writer to the shared production spool
- ``config.py``  — env-var driven settings (PIPE_TELEMETRY_*)
"""

from __future__ import annotations

from .emit import TELEMETRY_ACTION_ID_ENV, Action, action, emit
from .errors import (
    DCCLaunchError,
    HoudiniBuildError,
    PipelineError,
    PlayblastError,
    PublishCopyError,
    PublishPrecheckError,
    TextureConversionError,
    TextureExportError,
    USDExportError,
)
from .events import (
    EVENT_BUILD_HOUDINI_COMPONENT,
    EVENT_DCC_LAUNCH,
    EVENT_PLAYBLAST_CREATE,
    EVENT_PUBLISH_USD,
    EVENT_TEXTURE_CONVERT_TEX,
    EVENT_TEXTURE_EXPORT_SUBSTANCE,
    EVENT_DEFINITIONS,
    EVENTS_BY_TYPE,
    STATUS_ERROR,
    STATUS_SUCCESS,
    EventDefinition,
    Status,
    get_event_definition,
)
from .scope import SCOPE_FIELDS, ScopeContext, extract_scope

__all__ = [
    # Public API: workflow CM and bare emit
    "action",
    "Action",
    "emit",
    "TELEMETRY_ACTION_ID_ENV",
    # Scope helpers
    "extract_scope",
    "ScopeContext",
    "SCOPE_FIELDS",
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
    # Typed exceptions (each carries an `error_code` class attribute)
    "PipelineError",
    "DCCLaunchError",
    "PublishPrecheckError",
    "USDExportError",
    "PublishCopyError",
    "HoudiniBuildError",
    "TextureExportError",
    "TextureConversionError",
    "PlayblastError",
]
