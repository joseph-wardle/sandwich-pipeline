"""Telemetry runtime configuration, driven by `PIPE_TELEMETRY_*` env vars.

Defaults work without any env vars set. The knob that matters most:
`PIPE_TELEMETRY_ENABLED=0` disables emit entirely (returns a no-op writer).

The spool directory defaults to the shared production path
(`get_shared_telemetry_spool_dir()` in `pipeline/shared/util.py`). Override
with `PIPE_TELEMETRY_SPOOL_DIR` for tests or for the laptop POC.

`PIPE_TELEMETRY_RETENTION_DAYS=0` (the default) disables the spool retention
sweep — JSONL files persist forever, preserving the canonical record of
every event ever emitted. Set to a positive integer to opt into time-based
pruning if disk pressure becomes a concern.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.util import get_shared_telemetry_spool_dir


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _parse_bool(env_name: str, default: bool) -> bool:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _parse_int(env_name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"Invalid integer for {env_name}={raw!r}; expected an integer."
        ) from exc
    if value < minimum:
        raise ValueError(f"{env_name} must be >= {minimum}, got {value}.")
    return value


def _resolve_spool_dir() -> Path:
    override = os.environ.get("PIPE_TELEMETRY_SPOOL_DIR")
    if override:
        return Path(override).expanduser()
    return get_shared_telemetry_spool_dir()


@dataclass(frozen=True)
class TelemetryConfig:
    """Resolved telemetry settings for this process."""

    enabled: bool
    spool_dir: Path
    queue_max: int
    flush_seconds: float
    rotate_mb: int
    retention_days: int


def load_config() -> TelemetryConfig:
    """Read env vars and return a resolved config for this process."""

    return TelemetryConfig(
        enabled=_parse_bool("PIPE_TELEMETRY_ENABLED", default=True),
        spool_dir=_resolve_spool_dir(),
        queue_max=_parse_int("PIPE_TELEMETRY_QUEUE_MAX", default=5000, minimum=1),
        flush_seconds=_parse_int("PIPE_TELEMETRY_FLUSH_MS", default=1000, minimum=1)
        / 1000.0,
        rotate_mb=_parse_int("PIPE_TELEMETRY_ROTATE_MB", default=8, minimum=1),
        retention_days=_parse_int(
            "PIPE_TELEMETRY_RETENTION_DAYS", default=0, minimum=0
        ),
    )


__all__ = ["TelemetryConfig", "load_config"]
