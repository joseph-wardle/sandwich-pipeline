"""The two telemetry surfaces: `emit()` and the `action()` context manager.

`action(event_type, payload, scope=None)` wraps a workflow step:

    with telemetry.action("publish.usd", payload={"kind": "asset", ...}) as t:
        do_the_publish()                       # success: emits success on exit
        # raise USDExportError("...")          # error:   emits error on exit, re-raises

The CM emits exactly one terminal event when the block exits — `success` with
`duration_ms`, or `error` with the exception's `error_code`. It never
suppresses the exception.

`emit(event_type, status, payload, scope=None)` is for snapshot-shaped
events that don't have a success/error lifecycle (the periodic Tractor and
storage scanner pollers).
"""

from __future__ import annotations

import getpass
import logging
import os
import platform
import socket
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Final

from .events import (
    INFO_ONLY_STATUSES,
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_SUCCESS,
    WORKFLOW_STATUSES,
    EventDefinition,
    Status,
    get_event_definition,
)
from .spool import get_spool_writer

_LOG = logging.getLogger(__name__)

_UNKNOWN_ERROR_CODE: Final[str] = "UNKNOWN"


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string with `Z` suffix."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _resolve_user() -> str | None:
    try:
        return getpass.getuser()
    except OSError:
        return os.environ.get("USER") or os.environ.get("USERNAME")


def _resolve_hostname() -> str | None:
    return socket.gethostname() or platform.node() or None


def _validate_payload(
    definition: EventDefinition,
    status: Status,
    payload: Mapping[str, Any],
    *,
    strict: bool,
) -> bool:
    """Check `payload` against the registry contract. Returns True if valid.

    In strict mode, raises ValueError on contract violations (used in CI).
    In lenient mode (production default), logs a WARNING and returns False
    so the caller can drop the event.
    """

    if status not in definition.statuses:
        return _report_invalid(
            f"event {definition.event_type!r} does not allow status "
            f"{status!r}; allowed: {definition.statuses}",
            strict=strict,
        )

    missing = [
        field for field in definition.required_payload_fields if field not in payload
    ]
    if missing:
        return _report_invalid(
            f"event {definition.event_type!r} payload is missing required "
            f"fields: {missing}",
            strict=strict,
        )
    return True


def _report_invalid(message: str, *, strict: bool) -> bool:
    if strict:
        raise ValueError(message)
    _LOG.warning("Telemetry event rejected: %s", message)
    return False


def _build_event(
    *,
    event_type: str,
    status: Status,
    payload: Mapping[str, Any],
    scope: Mapping[str, str] | None,
    action_id: str,
    duration_ms: int | None,
    error_code: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    """Build the JSONL event row that the ingester will read."""

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "status": status,
        "occurred_at": _utc_now_iso(),
        "action_id": action_id,
        "hostname": _resolve_hostname(),
        "host_user": _resolve_user(),
        "dcc": os.environ.get("DCC"),
        "payload": dict(payload),
    }
    if scope:
        event["scope"] = dict(scope)
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    if error_code is not None:
        event["error_code"] = error_code
    if error_message is not None:
        event["error_message"] = error_message
    return event


def emit(
    event_type: str,
    *,
    status: Status,
    payload: Mapping[str, Any],
    scope: Mapping[str, str] | None = None,
    action_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Emit one telemetry event directly. Use for snapshot-shaped events only.

    For workflow-shaped events (publish, build, export, render), use
    `action()` — it handles success/error/timing correctly and can't be
    forgotten on the error path.
    """

    from .config import load_config

    config = load_config()
    definition = get_event_definition(event_type)
    if not _validate_payload(definition, status, payload, strict=config.strict):
        return

    event = _build_event(
        event_type=event_type,
        status=status,
        payload=payload,
        scope=scope,
        action_id=action_id or str(uuid.uuid4()),
        duration_ms=duration_ms,
        error_code=error_code,
        error_message=error_message,
    )
    get_spool_writer().write_event(event)


class Action:
    """Context manager for a workflow step that emits one terminal event.

    Construct via `action(event_type, payload, scope=None)`. Do not instantiate
    this class directly outside the telemetry module.
    """

    def __init__(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any],
        scope: Mapping[str, str] | None,
    ) -> None:
        self._definition = get_event_definition(event_type)
        if not set(self._definition.statuses) & set(WORKFLOW_STATUSES):
            raise ValueError(
                f"Event {event_type!r} is info-only and cannot be used with "
                f"action(); use emit() instead."
            )

        self._event_type = event_type
        self._payload: dict[str, Any] = dict(payload)
        self._scope: dict[str, str] | None = dict(scope) if scope else None
        self._action_id = str(uuid.uuid4())
        self._started_at: float = 0.0
        self._explicit_failure: tuple[str, str] | None = None

    @property
    def action_id(self) -> str:
        """Unique id for this action, useful when the caller needs to log it."""

        return self._action_id

    def update_payload(self, **kwargs: Any) -> None:
        """Add or overwrite payload fields mid-action.

        Common use: tagging `failed_tool` after a subprocess raises but before
        the exception propagates out of the `with` block.
        """

        self._payload.update(kwargs)

    def fail(self, error_code: str, message: str) -> None:
        """Explicitly mark this action as failed before raising.

        Use when the exception about to be raised does not carry an
        `error_code` attribute — typically stdlib exceptions, or contexts
        where one exception type means different things in different places.
        """

        self._explicit_failure = (error_code, message)

    def __enter__(self) -> Action:
        self._started_at = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        del exc_type, tb
        duration_ms = max(0, int((time.perf_counter() - self._started_at) * 1000))

        if exc is None and self._explicit_failure is None:
            self._emit_terminal(
                status=STATUS_SUCCESS,
                duration_ms=duration_ms,
                error_code=None,
                error_message=None,
            )
            return False

        if self._explicit_failure is not None:
            error_code, error_message = self._explicit_failure
        else:
            assert exc is not None
            error_code = getattr(exc, "error_code", _UNKNOWN_ERROR_CODE)
            error_message = str(exc) or exc.__class__.__name__

        self._emit_terminal(
            status=STATUS_ERROR,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )
        return False  # never suppress

    def _emit_terminal(
        self,
        *,
        status: Status,
        duration_ms: int,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        from .config import load_config

        config = load_config()
        if not _validate_payload(
            self._definition, status, self._payload, strict=config.strict
        ):
            return

        event = _build_event(
            event_type=self._event_type,
            status=status,
            payload=self._payload,
            scope=self._scope,
            action_id=self._action_id,
            duration_ms=duration_ms if self._definition.has_duration else None,
            error_code=error_code,
            error_message=error_message,
        )
        get_spool_writer().write_event(event)


def action(
    event_type: str,
    *,
    payload: Mapping[str, Any],
    scope: Mapping[str, str] | None = None,
) -> Action:
    """Wrap a workflow step in a telemetry action.

    Returns a context manager. On clean exit, emits a `success` event with
    `duration_ms`. On exception, emits an `error` event with `error_code`
    derived from the exception (`exc.error_code` if present, else `UNKNOWN`)
    and re-raises the exception unchanged.
    """

    return Action(event_type, payload=payload, scope=scope)


__all__ = [
    "STATUS_INFO",
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "INFO_ONLY_STATUSES",
    "Action",
    "action",
    "emit",
]
