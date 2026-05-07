"""The telemetry surfaces: the `record()` context manager and the bare `emit()`.

`record(event_type, payload=..., **entity_kwargs)` wraps a workflow step:

    from pipe import telemetry

    with telemetry.record(
        telemetry.EVENT_PUBLISH_USD,
        payload={"kind": "asset", "publish_path": str(path)},
        asset=asset,
    ) as telemetry_event:
        do_the_publish()                       # success: emits success on exit
        # raise SomePipelineError("...")       # error:   emits error on exit, re-raises

The CM emits exactly one terminal event when the block exits — `success` with
`duration_ms`, or `error` with the exception's `error_code`. It never
suppresses the exception.

`payload` carries event-specific facts (the metrics about *what happened*).
The `show`/`sequence`/`shot`/`asset`/`department` kwargs carry the entity
context (which production entity this event is *for*) and accept either a
ShotGrid entity object (with a `.code` attribute) or a plain string.

Failure classification is duck-typed: `record()` reads
`getattr(exc, "error_code")` from whatever exception escapes the block.
Workflow modules each define their own typed exceptions alongside the
raise sites and set `error_code` as a class attribute (see
`pipe.playblast.playblaster.PlayblastError`,
`pipe.m.publish.publisher.USDExportError`, etc.). Exceptions without the
attribute fall through to `error_code = "UNKNOWN"`. Call sites can
override on a case-by-case basis with `telemetry_event.fail(code, message)`
— typically when the work returns a structured result instead of raising.

`emit(event_type, status, payload, scope=None)` is the underlying primitive
for one-shot events that don't fit the workflow shape. No in-tree caller
uses it today; it remains available for code that needs to record a single
terminal event without a CM around it.
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

#: Env var the parent process sets so a child subprocess inherits its
#: action_id. Children read this at their entry point and skip their own
#: emission so the parent stays the sole emitter. Written by
#: `Event.attach_to_subprocess`; read by `pipe.h.assetbuilder.main`.
#: Internal — call sites use the method, not the constant.
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
    """Build the JSONL row that the ingester will read for this event.

    The returned dict is the on-disk shape; the public `Event` class is
    distinct from this dict and carries the in-progress workflow handle.
    """

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
    """Emit one telemetry event directly without a CM around it.

    For workflow-shaped events (publish, build, export, render), use
    `record()` — it handles success/error/timing correctly and can't be
    forgotten on the error path.
    """

    from .config import load_config

    config = load_config()
    definition = get_event_definition(event_type)
    if not _validate_payload(definition, status, payload, strict=config.strict):
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

    Construct via `record(event_type, payload=..., **entity_kwargs)`. Do
    not instantiate this class directly outside the telemetry module.

    Bound name at the call site is conventionally `telemetry_event`:

        with telemetry.record(...) as telemetry_event:
            do_the_work()
            telemetry_event.note(metric=value)        # final metrics
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
        """Unique id for this event. Useful for correlating subprocesses
        — see `attach_to_subprocess`."""

        return self._action_id

    def note(self, **kwargs: Any) -> None:
        """Add or overwrite payload fields on this event.

        Prefer one call at the bottom of the `with` block, after the work
        completes — so telemetry sits at the seam, not interleaved with
        publish logic. Mid-flight calls are allowed only when partial-metric
        fidelity on failure is genuinely useful, and should carry a comment
        explaining why that metric needed to escape early.
        """

        self._payload.update(kwargs)

    def fail(self, error_code: str, message: str) -> None:
        """Explicitly mark this event as failed before it exits.

        Use only when the work returns a structured result (no exception)
        and you've inspected it for failure. When the work raises a typed
        exception with an `error_code` class attribute, prefer letting that
        classify the failure automatically — do not call `fail()`.
        """

        self._explicit_failure = (error_code, message)

    def attach_to_subprocess(self, env: dict[str, str]) -> None:
        """Mutate `env` so a child subprocess correlates with this event.

        The child reads the env var at its entry point and skips its own
        emission, so the parent's `record()` block remains the sole emitter
        for this action_id. Intended use: pass `env` straight into
        `subprocess.run`/`Popen` after this call.
        """

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
        from .config import load_config

        config = load_config()
        if not _validate_payload(
            self._definition, status, self._payload, strict=config.strict
        ):
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

    Returns a context manager. On clean exit, emits a `success` event with
    `duration_ms`. On exception, emits an `error` event with `error_code`
    derived from the exception (`exc.error_code` if present, else `UNKNOWN`)
    and re-raises the exception unchanged.

    `payload` is the dict of event-specific facts (the metrics about *what
    happened*). The five entity kwargs name the production entities this
    event is *for* — each accepts a ShotGrid entity object (with a `.code`
    attribute) or a plain string; pass only the dimensions that apply.
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
    """Return True when a parent process is wrapping this one in its own event.

    DCC subprocesses (e.g. `pipe.h.assetbuilder` launched from Maya) check
    this at their entry point. When the parent has set the correlation env
    var via `Event.attach_to_subprocess`, the child must skip its own
    emission so the event isn't double-counted.
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
