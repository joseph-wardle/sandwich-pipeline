"""Telemetry envelope validation, sanitization, and size controls."""

from __future__ import annotations

import json
import re
from typing import Any, Final, Mapping

from .context import SCOPE_FIELDS
from .registry import SCHEMA_VERSION, STATUS_ERROR, get_event_definition

REQUIRED_ENVELOPE_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "event_id",
    "event_type",
    "occurred_at_utc",
    "status",
    "pipeline",
    "host",
    "session",
    "payload",
)
REQUIRED_ERROR_KEYS: Final[tuple[str, ...]] = ("code", "message")

DEFAULT_MAX_STRING_CHARS: Final[int] = 2048
DEFAULT_MAX_ERROR_MESSAGE_CHARS: Final[int] = 1024
DEFAULT_MAX_STACKTRACE_CHARS: Final[int] = 4096
DEFAULT_MAX_MAPPING_ITEMS: Final[int] = 128
DEFAULT_MAX_SEQUENCE_ITEMS: Final[int] = 128
DEFAULT_MAX_DEPTH: Final[int] = 6

_TRUNCATED_SUFFIX: Final[str] = "...(truncated)"
_REDACTED_VALUE: Final[str] = "[REDACTED]"

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|apikey|auth|credential|cookie|private[_-]?key|shotgrid[_-]?(script_)?key)",
    re.IGNORECASE,
)
_ENVIRONMENT_KEY_PATTERN = re.compile(
    r"^(env|environment|environ|env_vars|environment_vars|environment_variables)$",
    re.IGNORECASE,
)


class TelemetryContractError(ValueError):
    """Base class for telemetry contract enforcement failures."""


class EventValidationError(TelemetryContractError):
    """Raised when event structure or required fields are invalid."""


class EventTooLargeError(TelemetryContractError):
    """Raised when event cannot be reduced below configured byte cap."""


def compact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy without ``None`` values."""

    return {key: value for key, value in data.items() if value is not None}


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars < 1:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATED_SUFFIX):
        return _TRUNCATED_SUFFIX[:max_chars]
    head_chars = max_chars - len(_TRUNCATED_SUFFIX)
    return text[:head_chars] + _TRUNCATED_SUFFIX


def _sanitize_environment_dump(
    value: Any, *, max_sequence_items: int
) -> dict[str, Any] | str:
    if isinstance(value, Mapping):
        keys = sorted(str(key) for key in value.keys())
        return {
            "keys": keys[:max_sequence_items],
            "count": len(keys),
            "values_redacted": True,
        }
    if isinstance(value, (list, tuple, set)):
        return {
            "count": len(value),
            "values_redacted": True,
        }
    return _REDACTED_VALUE


def _sanitize_value(
    value: Any,
    *,
    key_name: str,
    depth: int,
    max_string_chars: int,
    max_mapping_items: int,
    max_sequence_items: int,
    max_depth: int,
) -> Any:
    if depth > max_depth:
        return _truncate_text(str(value), max_string_chars)

    if _SENSITIVE_KEY_PATTERN.search(key_name):
        return _REDACTED_VALUE

    if _ENVIRONMENT_KEY_PATTERN.match(key_name):
        return _sanitize_environment_dump(value, max_sequence_items=max_sequence_items)

    if isinstance(value, str):
        return _truncate_text(value, max_string_chars)
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, bytearray):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value

    if isinstance(value, Mapping):
        items = list(value.items())
        sanitized_map: dict[str, Any] = {}
        for index, (child_key, child_value) in enumerate(items):
            if index >= max_mapping_items:
                sanitized_map["_truncated_keys_count"] = len(items) - max_mapping_items
                break
            normalized_key = str(child_key)
            sanitized_map[normalized_key] = _sanitize_value(
                child_value,
                key_name=normalized_key,
                depth=depth + 1,
                max_string_chars=max_string_chars,
                max_mapping_items=max_mapping_items,
                max_sequence_items=max_sequence_items,
                max_depth=max_depth,
            )
        return sanitized_map

    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        sanitized_sequence: list[Any] = []
        for index, item in enumerate(sequence):
            if index >= max_sequence_items:
                sanitized_sequence.append(
                    f"<truncated_items:{len(sequence) - max_sequence_items}>"
                )
                break
            sanitized_sequence.append(
                _sanitize_value(
                    item,
                    key_name=key_name,
                    depth=depth + 1,
                    max_string_chars=max_string_chars,
                    max_mapping_items=max_mapping_items,
                    max_sequence_items=max_sequence_items,
                    max_depth=max_depth,
                )
            )
        return sanitized_sequence

    return _truncate_text(str(value), max_string_chars)


def sanitize_event(
    event: Mapping[str, Any],
    *,
    include_stacktrace: bool,
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
    max_mapping_items: int = DEFAULT_MAX_MAPPING_ITEMS,
    max_sequence_items: int = DEFAULT_MAX_SEQUENCE_ITEMS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, Any]:
    """Return sanitized event payload with redaction and truncation applied."""

    sanitized: dict[str, Any] = {}
    for key, value in event.items():
        normalized_key = str(key)
        sanitized[normalized_key] = _sanitize_value(
            value,
            key_name=normalized_key,
            depth=0,
            max_string_chars=max_string_chars,
            max_mapping_items=max_mapping_items,
            max_sequence_items=max_sequence_items,
            max_depth=max_depth,
        )

    error = sanitized.get("error")
    if isinstance(error, Mapping):
        sanitized["error"] = normalize_error(
            error,
            include_stacktrace=include_stacktrace,
            max_message_chars=min(DEFAULT_MAX_ERROR_MESSAGE_CHARS, max_string_chars),
            max_stacktrace_chars=min(
                DEFAULT_MAX_STACKTRACE_CHARS, max_string_chars * 2
            ),
        )

    return sanitized


def validate_envelope(event: Mapping[str, Any]) -> None:
    """Validate required envelope fields and registry contract compliance."""

    missing = [key for key in REQUIRED_ENVELOPE_KEYS if key not in event]
    if missing:
        raise EventValidationError(f"Event envelope missing required fields: {missing}")

    for text_key in (
        "schema_version",
        "event_id",
        "event_type",
        "occurred_at_utc",
        "status",
    ):
        value = event[text_key]
        if not isinstance(value, str) or not value.strip():
            raise EventValidationError(
                f"Event envelope field '{text_key}' must be a non-empty string"
            )

    if event["schema_version"] != SCHEMA_VERSION:
        raise EventValidationError(
            f"Unsupported schema version {event['schema_version']!r}; "
            f"expected {SCHEMA_VERSION!r}"
        )

    event_type = str(event["event_type"])
    status = str(event["status"])
    definition = get_event_definition(event_type)

    if status not in definition.status_values:
        raise EventValidationError(
            f"Status '{status}' not allowed for event '{event_type}'. "
            f"Allowed: {definition.status_values}"
        )

    for mapping_key in ("pipeline", "host", "session"):
        if not isinstance(event.get(mapping_key), Mapping):
            raise EventValidationError(
                f"Event '{event_type}' field '{mapping_key}' must be a mapping"
            )

    session = event["session"]
    session_id = session.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise EventValidationError(
            f"Event '{event_type}' session.session_id must be a non-empty string"
        )
    action_id = session.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        raise EventValidationError(
            f"Event '{event_type}' session.action_id must be a non-empty string"
        )

    scope = event.get("scope")
    if scope is not None and not isinstance(scope, Mapping):
        raise EventValidationError(f"Event '{event_type}' scope must be a mapping")
    if isinstance(scope, Mapping):
        unknown_scope_fields = [
            str(key) for key in scope.keys() if str(key) not in SCOPE_FIELDS
        ]
        if unknown_scope_fields:
            raise EventValidationError(
                f"Event '{event_type}' scope has unsupported fields: {unknown_scope_fields}. "
                f"Allowed: {SCOPE_FIELDS}"
            )
        for scope_key in SCOPE_FIELDS:
            if scope_key not in scope:
                continue
            scope_value = scope.get(scope_key)
            if scope_value is None:
                continue
            if not isinstance(scope_value, str) or not scope_value.strip():
                raise EventValidationError(
                    f"Event '{event_type}' scope.{scope_key} must be a non-empty string "
                    "when provided"
                )

    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise EventValidationError(f"Event '{event_type}' payload must be a mapping")

    missing_payload_fields = [
        field for field in definition.required_payload_fields if field not in payload
    ]
    if missing_payload_fields:
        raise EventValidationError(
            f"Event '{event_type}' missing required payload fields: {missing_payload_fields}"
        )

    metrics = event.get("metrics")
    if metrics is not None and not isinstance(metrics, Mapping):
        raise EventValidationError(
            f"Event '{event_type}' metrics must be a mapping when provided"
        )

    if definition.required_metrics_fields:
        if not isinstance(metrics, Mapping):
            raise EventValidationError(
                f"Event '{event_type}' requires metrics fields {definition.required_metrics_fields}"
            )
        missing_metrics_fields = [
            field
            for field in definition.required_metrics_fields
            if field not in metrics
        ]
        if missing_metrics_fields:
            raise EventValidationError(
                f"Event '{event_type}' missing required metrics fields: {missing_metrics_fields}"
            )

    if status == STATUS_ERROR:
        error = event.get("error")
        if not isinstance(error, Mapping):
            raise EventValidationError(
                f"Event '{event_type}' status='error' requires error object"
            )
        missing_error_keys = [key for key in REQUIRED_ERROR_KEYS if key not in error]
        if missing_error_keys:
            raise EventValidationError(
                f"Event '{event_type}' error object missing required fields: "
                f"{missing_error_keys}"
            )
        error_code = str(error.get("code"))
        if not error_code.strip():
            raise EventValidationError(
                f"Event '{event_type}' error.code must be a non-empty string"
            )
        if definition.error_codes and error_code not in definition.error_codes:
            raise EventValidationError(
                f"Event '{event_type}' uses unsupported error code '{error_code}'. "
                f"Allowed: {definition.error_codes}"
            )


def normalize_error(
    error: Mapping[str, Any],
    *,
    include_stacktrace: bool = False,
    max_message_chars: int = DEFAULT_MAX_ERROR_MESSAGE_CHARS,
    max_stacktrace_chars: int = DEFAULT_MAX_STACKTRACE_CHARS,
) -> dict[str, Any]:
    """Return normalized error payload with bounded text fields."""

    normalized: dict[str, Any] = compact_mapping(
        {
            "code": _truncate_text(str(error.get("code", "")), max_message_chars),
            "message": _truncate_text(str(error.get("message", "")), max_message_chars),
            "exception_type": _truncate_text(
                str(error.get("exception_type", "")), max_message_chars
            )
            if error.get("exception_type") is not None
            else None,
            "stacktrace": _truncate_text(
                str(error.get("stacktrace", "")), max_stacktrace_chars
            )
            if include_stacktrace and error.get("stacktrace") is not None
            else None,
        }
    )

    missing = [key for key in REQUIRED_ERROR_KEYS if key not in normalized]
    if missing:
        raise EventValidationError(f"Error payload missing required keys: {missing}")
    return normalized


def serialize_event(event: Mapping[str, Any]) -> str:
    """Serialize event deterministically for disk/appended transport."""

    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def event_size_bytes(event: Mapping[str, Any]) -> int:
    """Return serialized event size in bytes."""

    return len(serialize_event(event).encode("utf-8"))


def _truncate_strings(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, max_chars)
    if isinstance(value, Mapping):
        return {
            str(key): _truncate_strings(item, max_chars) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_truncate_strings(item, max_chars) for item in value]
    if isinstance(value, tuple):
        return tuple(_truncate_strings(item, max_chars) for item in value)
    return value


def truncate_event_to_size(
    event: Mapping[str, Any],
    *,
    max_event_bytes: int,
    required_payload_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return event truncated to fit ``max_event_bytes`` when possible."""

    candidate: dict[str, Any] = dict(event)
    if event_size_bytes(candidate) <= max_event_bytes:
        return candidate

    for max_chars in (1024, 512, 256, 128, 64):
        candidate = _truncate_strings(candidate, max_chars)
        if event_size_bytes(candidate) <= max_event_bytes:
            return candidate

    payload = candidate.get("payload")
    if isinstance(payload, dict):
        optional_keys = [
            key for key in payload.keys() if key not in required_payload_fields
        ]
        optional_keys.sort(key=lambda key: len(str(payload.get(key, ""))), reverse=True)
        removed = 0
        for key in optional_keys:
            payload.pop(key, None)
            removed += 1
            if event_size_bytes(candidate) <= max_event_bytes:
                payload["_dropped_payload_fields_count"] = removed
                return candidate

    if event_size_bytes(candidate) <= max_event_bytes:
        return candidate

    raise EventTooLargeError(
        f"Event size exceeds PIPE_TELEMETRY_MAX_EVENT_BYTES={max_event_bytes} "
        "after truncation"
    )


def enforce_max_event_size(event: Mapping[str, Any], max_event_bytes: int) -> None:
    """Raise if serialized event exceeds configured maximum size."""

    size = event_size_bytes(event)
    if size > max_event_bytes:
        raise EventTooLargeError(
            f"Event size {size} exceeds PIPE_TELEMETRY_MAX_EVENT_BYTES={max_event_bytes}"
        )


__all__ = [
    "REQUIRED_ENVELOPE_KEYS",
    "REQUIRED_ERROR_KEYS",
    "TelemetryContractError",
    "EventValidationError",
    "EventTooLargeError",
    "compact_mapping",
    "sanitize_event",
    "validate_envelope",
    "normalize_error",
    "serialize_event",
    "event_size_bytes",
    "truncate_event_to_size",
    "enforce_max_event_size",
]
