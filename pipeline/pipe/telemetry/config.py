"""Telemetry runtime configuration.

This module centralizes environment-driven telemetry settings so callsites do
not read ``PIPE_TELEMETRY_*`` variables directly.
"""

from __future__ import annotations

import logging
import os
import platform
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Mapping, Optional, TypeVar

TelemetryLevel = Literal["minimal", "standard", "verbose"]
PlatformFlavor = Literal["linux", "windows", "other"]

_LOG = logging.getLogger(__name__)

_TRUE_VALUES: Final[set[str]] = {"1", "true", "yes", "on"}
_FALSE_VALUES: Final[set[str]] = {"0", "false", "no", "off"}
_LEVEL_VALUES: Final[tuple[TelemetryLevel, ...]] = ("minimal", "standard", "verbose")

_DEFAULT_ENABLED: Final[bool] = True
_DEFAULT_LEVEL: Final[TelemetryLevel] = "standard"
_DEFAULT_QUEUE_MAX: Final[int] = 5000
_DEFAULT_FLUSH_MS: Final[int] = 1000
_DEFAULT_MAX_EVENT_BYTES: Final[int] = 65536
_DEFAULT_ROTATE_MB: Final[int] = 8
_DEFAULT_RETENTION_DAYS: Final[int] = 7
_DEFAULT_INCLUDE_STACKTRACE: Final[bool] = False

_T = TypeVar("_T")


def detect_platform_flavor(
    *, os_name: Optional[str] = None, system_name: Optional[str] = None
) -> PlatformFlavor:
    """Return normalized platform flavor for spool path policy."""

    resolved_os_name = os.name if os_name is None else os_name
    resolved_system_name = platform.system() if system_name is None else system_name
    lowered_system = resolved_system_name.lower()

    if resolved_os_name == "nt" or lowered_system.startswith("win"):
        return "windows"
    if resolved_os_name == "posix":
        return "linux"
    return "other"


def _safe_home(home_dir: Optional[Path]) -> Optional[Path]:
    if home_dir is not None:
        return home_dir.expanduser()
    try:
        return Path.home()
    except Exception:
        return None


def _parse_bool(name: str, raw_value: Optional[str], default: bool) -> bool:
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"Invalid boolean value for {name}: {raw_value!r}. "
        f"Expected one of {_TRUE_VALUES | _FALSE_VALUES}."
    )


def _parse_int(
    name: str, raw_value: Optional[str], default: int, *, minimum: int
) -> int:
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid integer value for {name}: {raw_value!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _parse_level(raw_value: Optional[str]) -> TelemetryLevel:
    if raw_value is None:
        return _DEFAULT_LEVEL
    normalized = raw_value.strip().lower()
    if normalized not in _LEVEL_VALUES:
        raise ValueError(
            f"Invalid PIPE_TELEMETRY_LEVEL value {raw_value!r}. "
            f"Expected one of {_LEVEL_VALUES}."
        )
    return normalized  # type: ignore


def _parse_or_default(
    *,
    name: str,
    raw_value: Optional[str],
    default: _T,
    strict: bool,
    warnings: list[str],
    parse_fn: Callable[[], _T],
) -> _T:
    try:
        return parse_fn()
    except ValueError as exc:
        if strict:
            raise
        message = (
            f"Invalid telemetry config {name}={raw_value!r}; "
            f"using default {default!r} ({exc})"
        )
        warnings.append(message)
        _LOG.warning(message)
        return default


def default_spool_dir(
    env: Optional[Mapping[str, str]] = None,
    *,
    platform_flavor: Optional[PlatformFlavor] = None,
    home_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None,
) -> Path:
    """Return deterministic default telemetry spool directory."""

    source_env = env if env is not None else os.environ

    override = source_env.get("PIPE_TELEMETRY_SPOOL_DIR")
    if override:
        return Path(override).expanduser()

    flavor = detect_platform_flavor() if platform_flavor is None else platform_flavor

    if flavor == "linux":
        xdg_state_home = source_env.get("XDG_STATE_HOME")
        if xdg_state_home:
            base = Path(xdg_state_home).expanduser()
            return base / "sandwich-pipeline" / "telemetry"

        resolved_home = _safe_home(home_dir)
        if resolved_home is not None:
            return (
                resolved_home / ".local" / "state" / "sandwich-pipeline" / "telemetry"
            )

    if flavor == "windows":
        local_app_data = source_env.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "sandwich-pipeline" / "telemetry"

    fallback_temp = (
        str(temp_dir)
        if temp_dir is not None
        else (
            source_env.get("TMPDIR") or source_env.get("TEMP") or tempfile.gettempdir()
        )
    )
    return Path(fallback_temp) / "sandwich-pipeline-telemetry"


@dataclass(frozen=True)
class TelemetryConfig:
    """Immutable telemetry settings resolved from environment variables."""

    enabled: bool
    level: TelemetryLevel
    spool_dir: Path
    queue_max: int
    flush_ms: int
    max_event_bytes: int
    rotate_mb: int
    retention_days: int
    include_stacktrace: bool
    platform_flavor: PlatformFlavor
    parse_warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        strict: bool = False,
        platform_flavor: Optional[PlatformFlavor] = None,
        home_dir: Optional[Path] = None,
        temp_dir: Optional[Path] = None,
    ) -> "TelemetryConfig":
        source_env = env if env is not None else os.environ
        resolved_platform = (
            detect_platform_flavor() if platform_flavor is None else platform_flavor
        )
        parse_warnings: list[str] = []

        enabled = _parse_or_default(
            name="PIPE_TELEMETRY_ENABLED",
            raw_value=source_env.get("PIPE_TELEMETRY_ENABLED"),
            default=_DEFAULT_ENABLED,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_bool(
                "PIPE_TELEMETRY_ENABLED",
                source_env.get("PIPE_TELEMETRY_ENABLED"),
                _DEFAULT_ENABLED,
            ),
        )
        level = _parse_or_default(
            name="PIPE_TELEMETRY_LEVEL",
            raw_value=source_env.get("PIPE_TELEMETRY_LEVEL"),
            default=_DEFAULT_LEVEL,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_level(source_env.get("PIPE_TELEMETRY_LEVEL")),
        )
        queue_max = _parse_or_default(
            name="PIPE_TELEMETRY_QUEUE_MAX",
            raw_value=source_env.get("PIPE_TELEMETRY_QUEUE_MAX"),
            default=_DEFAULT_QUEUE_MAX,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_int(
                "PIPE_TELEMETRY_QUEUE_MAX",
                source_env.get("PIPE_TELEMETRY_QUEUE_MAX"),
                _DEFAULT_QUEUE_MAX,
                minimum=1,
            ),
        )
        flush_ms = _parse_or_default(
            name="PIPE_TELEMETRY_FLUSH_MS",
            raw_value=source_env.get("PIPE_TELEMETRY_FLUSH_MS"),
            default=_DEFAULT_FLUSH_MS,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_int(
                "PIPE_TELEMETRY_FLUSH_MS",
                source_env.get("PIPE_TELEMETRY_FLUSH_MS"),
                _DEFAULT_FLUSH_MS,
                minimum=1,
            ),
        )
        max_event_bytes = _parse_or_default(
            name="PIPE_TELEMETRY_MAX_EVENT_BYTES",
            raw_value=source_env.get("PIPE_TELEMETRY_MAX_EVENT_BYTES"),
            default=_DEFAULT_MAX_EVENT_BYTES,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_int(
                "PIPE_TELEMETRY_MAX_EVENT_BYTES",
                source_env.get("PIPE_TELEMETRY_MAX_EVENT_BYTES"),
                _DEFAULT_MAX_EVENT_BYTES,
                minimum=1024,
            ),
        )
        rotate_mb = _parse_or_default(
            name="PIPE_TELEMETRY_ROTATE_MB",
            raw_value=source_env.get("PIPE_TELEMETRY_ROTATE_MB"),
            default=_DEFAULT_ROTATE_MB,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_int(
                "PIPE_TELEMETRY_ROTATE_MB",
                source_env.get("PIPE_TELEMETRY_ROTATE_MB"),
                _DEFAULT_ROTATE_MB,
                minimum=1,
            ),
        )
        retention_days = _parse_or_default(
            name="PIPE_TELEMETRY_RETENTION_DAYS",
            raw_value=source_env.get("PIPE_TELEMETRY_RETENTION_DAYS"),
            default=_DEFAULT_RETENTION_DAYS,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_int(
                "PIPE_TELEMETRY_RETENTION_DAYS",
                source_env.get("PIPE_TELEMETRY_RETENTION_DAYS"),
                _DEFAULT_RETENTION_DAYS,
                minimum=1,
            ),
        )
        include_stacktrace = _parse_or_default(
            name="PIPE_TELEMETRY_INCLUDE_STACKTRACE",
            raw_value=source_env.get("PIPE_TELEMETRY_INCLUDE_STACKTRACE"),
            default=_DEFAULT_INCLUDE_STACKTRACE,
            strict=strict,
            warnings=parse_warnings,
            parse_fn=lambda: _parse_bool(
                "PIPE_TELEMETRY_INCLUDE_STACKTRACE",
                source_env.get("PIPE_TELEMETRY_INCLUDE_STACKTRACE"),
                _DEFAULT_INCLUDE_STACKTRACE,
            ),
        )

        return cls(
            enabled=enabled,
            level=level,
            spool_dir=default_spool_dir(
                source_env,
                platform_flavor=resolved_platform,
                home_dir=home_dir,
                temp_dir=temp_dir,
            ),
            queue_max=queue_max,
            flush_ms=flush_ms,
            max_event_bytes=max_event_bytes,
            rotate_mb=rotate_mb,
            retention_days=retention_days,
            include_stacktrace=include_stacktrace,
            platform_flavor=resolved_platform,
            parse_warnings=tuple(parse_warnings),
        )


_CONFIG_CACHE: Optional[TelemetryConfig] = None


def load_config(
    *,
    force_reload: bool = False,
    env: Optional[Mapping[str, str]] = None,
    strict: bool = False,
    platform_flavor: Optional[PlatformFlavor] = None,
    home_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None,
) -> TelemetryConfig:
    """Load telemetry config with fail-open defaults by default.

    ``strict=True`` can be used in tests/CI to fail fast on invalid values.
    """

    global _CONFIG_CACHE

    needs_uncached_resolution = (
        env is not None
        or strict
        or platform_flavor is not None
        or home_dir is not None
        or temp_dir is not None
    )

    if needs_uncached_resolution:
        return TelemetryConfig.from_env(
            env,
            strict=strict,
            platform_flavor=platform_flavor,
            home_dir=home_dir,
            temp_dir=temp_dir,
        )

    if _CONFIG_CACHE is None or force_reload:
        _CONFIG_CACHE = TelemetryConfig.from_env()
    return _CONFIG_CACHE


__all__ = [
    "TelemetryLevel",
    "PlatformFlavor",
    "TelemetryConfig",
    "detect_platform_flavor",
    "default_spool_dir",
    "load_config",
]
