"""Implementation of `record()`, the `Event` context manager, and bare `emit()`."""

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
    STATUS_ERROR,
    STATUS_SUCCESS,
    EventDefinition,
    Status,
    get_event_definition,
)
from .scope import _build_scope_dict
from .spool import get_spool_writer

_LOG = logging.getLogger(__name__)

_UNKNOWN_ERROR_CODE: Final[str] = "UNKNOWN"

#: Internal env var carrying the parent's action_id into a child subprocess.
_ACTION_ID_ENV: Final[str] = "PIPE_TELEMETRY_ACTION_ID"


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
) -> bool:
    """Check `payload` against the event's required fields.

    Returns True if valid. On invalid input, logs a warning and returns
    False so the caller drops the event.
    """

    if status not in definition.statuses:
        _LOG.warning(
            "Telemetry event %r does not allow status %r; allowed: %s",
            definition.event_type,
            status,
            definition.statuses,
        )
        return False

    missing = [
        field for field in definition.required_payload_fields if field not in payload
    ]
    if missing:
        _LOG.warning(
            "Telemetry event %r payload is missing required fields: %s",
            definition.event_type,
            missing,
        )
        return False
    return True


def _build_event_row(
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
    """Build the JSONL row that the ingester will read for this event."""

    row: dict[str, Any] = {
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
        row["scope"] = dict(scope)
    if duration_ms is not None:
        row["duration_ms"] = duration_ms
    if error_code is not None:
        row["error_code"] = error_code
    if error_message is not None:
        row["error_message"] = error_message
    return row


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
    """Emit one telemetry event directly, without wrapping a `with` block.

    Prefer `record()` for workflow steps — it handles timing and the
    error path automatically.
    """

    definition = get_event_definition(event_type)
    if not _validate_payload(definition, status, payload):
        return

    row = _build_event_row(
        event_type=event_type,
        status=status,
        payload=payload,
        scope=scope,
        action_id=action_id or str(uuid.uuid4()),
        duration_ms=duration_ms,
        error_code=error_code,
        error_message=error_message,
    )
    get_spool_writer().write_event(row)


class Event:
    """An in-progress telemetry event for one workflow step.

    Construct via `record(...)`; the bound name is conventionally
    `telemetry_event`:

        with telemetry.record(...) as telemetry_event:
            do_the_work()
            telemetry_event.update(metric=value)
    """

    def __init__(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any],
        scope: Mapping[str, str] | None,
    ) -> None:
        self._definition = get_event_definition(event_type)
        self._event_type = event_type
        self._payload: dict[str, Any] = dict(payload)
        self._scope: dict[str, str] | None = dict(scope) if scope else None
        self._action_id = str(uuid.uuid4())
        self._started_at: float = 0.0
        self._explicit_failure: tuple[str, str] | None = None

    @property
    def action_id(self) -> str:
        """Unique id for this event. See `attach_to_subprocess` for use."""

        return self._action_id

    def update(self, **kwargs: Any) -> None:
        """Add or overwrite payload fields on this event."""

        self._payload.update(kwargs)

    def fail(self, error_code: str, message: str) -> None:
        """Mark this event failed with the given error code and message.

        Use only when the work returns a result instead of raising. When
        a typed exception with `error_code` is available, let it classify
        the failure automatically — don't call this.
        """

        self._explicit_failure = (error_code, message)

    def attach_to_subprocess(self, env: dict[str, str]) -> None:
        """Mutate `env` so a child subprocess correlates with this event."""

        env[_ACTION_ID_ENV] = self._action_id

    def __enter__(self) -> Event:
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
        if not _validate_payload(self._definition, status, self._payload):
            return

        row = _build_event_row(
            event_type=self._event_type,
            status=status,
            payload=self._payload,
            scope=self._scope,
            action_id=self._action_id,
            duration_ms=duration_ms if self._definition.has_duration else None,
            error_code=error_code,
            error_message=error_message,
        )
        get_spool_writer().write_event(row)


def record(
    event_type: str,
    *,
    payload: Mapping[str, Any],
    show: object | None = None,
    sequence: object | None = None,
    shot: object | None = None,
    asset: object | None = None,
    department: object | None = None,
) -> Event:
    """Wrap a workflow step in a telemetry event.

    Returns a context manager. On exit, emits a `success` event with
    `duration_ms`. On exception, emits an `error` event with `error_code`
    derived from the exception

    `payload` is event-specific data (what happened). The entity kwargs
    attach this event to a show/sequence/shot/asset/department; each
    accepts a ShotGrid entity or a plain string. Pass only what applies.
    """

    scope = _build_scope_dict(
        show=show,
        sequence=sequence,
        shot=shot,
        asset=asset,
        department=department,
    )
    return Event(event_type, payload=payload, scope=scope or None)


def _running_under_parent_event() -> bool:
    """Return True if a parent process is already recording this work.

    DCC subprocesses check this at their entry point and skip their own
    emission when set, so the event isn't double-counted.
    """

    return bool(os.getenv(_ACTION_ID_ENV, "").strip())


__all__ = [
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "Event",
    "record",
    "emit",
    "_running_under_parent_event",
]
