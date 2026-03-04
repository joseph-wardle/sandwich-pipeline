"""Canonical telemetry contract for pipeline-side event emission.

This module is intentionally static and self-documenting. All event type names,
status enums, required payload fields, and stable error codes are declared here
so downstream parsing can remain stable across implementation changes.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Final, Literal

SCHEMA_VERSION: Final[str] = "1.0"

StatusValue = Literal["success", "error", "warning", "info"]

STATUS_SUCCESS: Final[StatusValue] = "success"
STATUS_ERROR: Final[StatusValue] = "error"
STATUS_WARNING: Final[StatusValue] = "warning"
STATUS_INFO: Final[StatusValue] = "info"

STATUS_VALUES: Final[tuple[StatusValue, ...]] = (
    STATUS_SUCCESS,
    STATUS_ERROR,
    STATUS_WARNING,
    STATUS_INFO,
)

TERMINAL_STATUS_VALUES: Final[tuple[StatusValue, ...]] = (
    STATUS_SUCCESS,
    STATUS_ERROR,
)

EVENT_TYPE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_]+)+$")


# Stable error code taxonomy (v1.0).
ERROR_DCC_LAUNCH_FAILED: Final[str] = "DCC_LAUNCH_FAILED"
ERROR_PUBLISH_PRECHECK_FAILED: Final[str] = "PUBLISH_PRECHECK_FAILED"
ERROR_USD_EXPORT_FAILED: Final[str] = "USD_EXPORT_FAILED"
ERROR_PUBLISH_COPY_FAILED: Final[str] = "PUBLISH_COPY_FAILED"
ERROR_WINDOWS_MOVE_FAILED: Final[str] = "WINDOWS_MOVE_FAILED"
ERROR_HOUDINI_BUILD_FAILED: Final[str] = "HOUDINI_BUILD_FAILED"
ERROR_HOUDINI_BUILD_RESULT_PARSE_FAILED: Final[str] = (
    "HOUDINI_BUILD_RESULT_PARSE_FAILED"
)
ERROR_TEXTURE_EXPORT_FAILED: Final[str] = "TEXTURE_EXPORT_FAILED"
ERROR_TEXTURE_CONVERSION_FAILED: Final[str] = "TEXTURE_CONVERSION_FAILED"
ERROR_FILE_OPEN_FAILED: Final[str] = "FILE_OPEN_FAILED"
ERROR_FILE_CREATE_FAILED: Final[str] = "FILE_CREATE_FAILED"
ERROR_SHOT_SETUP_FAILED: Final[str] = "SHOT_SETUP_FAILED"
ERROR_PLAYBLAST_FAILED: Final[str] = "PLAYBLAST_FAILED"
ERROR_TRACTOR_SPOOL_FAILED: Final[str] = "TRACTOR_SPOOL_FAILED"
ERROR_TRACTOR_SNAPSHOT_FAILED: Final[str] = "TRACTOR_SNAPSHOT_FAILED"
ERROR_RENDER_STATS_HARVEST_FAILED: Final[str] = "RENDER_STATS_HARVEST_FAILED"
ERROR_STORAGE_SCAN_FAILED: Final[str] = "STORAGE_SCAN_FAILED"
ERROR_STORAGE_CLASSIFICATION_FAILED: Final[str] = "STORAGE_CLASSIFICATION_FAILED"

ERROR_CODES: Final[tuple[str, ...]] = (
    ERROR_DCC_LAUNCH_FAILED,
    ERROR_PUBLISH_PRECHECK_FAILED,
    ERROR_USD_EXPORT_FAILED,
    ERROR_PUBLISH_COPY_FAILED,
    ERROR_WINDOWS_MOVE_FAILED,
    ERROR_HOUDINI_BUILD_FAILED,
    ERROR_HOUDINI_BUILD_RESULT_PARSE_FAILED,
    ERROR_TEXTURE_EXPORT_FAILED,
    ERROR_TEXTURE_CONVERSION_FAILED,
    ERROR_FILE_OPEN_FAILED,
    ERROR_FILE_CREATE_FAILED,
    ERROR_SHOT_SETUP_FAILED,
    ERROR_PLAYBLAST_FAILED,
    ERROR_TRACTOR_SPOOL_FAILED,
    ERROR_TRACTOR_SNAPSHOT_FAILED,
    ERROR_RENDER_STATS_HARVEST_FAILED,
    ERROR_STORAGE_SCAN_FAILED,
    ERROR_STORAGE_CLASSIFICATION_FAILED,
)


@dataclass(frozen=True)
class EventDefinition:
    """Definition of one telemetry event type in the v1.0 contract."""

    event_type: str
    description: str
    owner_module: str
    required_payload_fields: tuple[str, ...] = ()
    required_metrics_fields: tuple[str, ...] = ()
    status_values: tuple[StatusValue, ...] = TERMINAL_STATUS_VALUES
    error_codes: tuple[str, ...] = ()
    sample_rate: float = 1.0


def _event(
    event_type: str,
    description: str,
    owner_module: str,
    *,
    required_payload_fields: tuple[str, ...] = (),
    required_metrics_fields: tuple[str, ...] = (),
    status_values: tuple[StatusValue, ...] = TERMINAL_STATUS_VALUES,
    error_codes: tuple[str, ...] = (),
    sample_rate: float = 1.0,
) -> EventDefinition:
    return EventDefinition(
        event_type=event_type,
        description=description,
        owner_module=owner_module,
        required_payload_fields=required_payload_fields,
        required_metrics_fields=required_metrics_fields,
        status_values=status_values,
        error_codes=error_codes,
        sample_rate=sample_rate,
    )


_EVENT_DEFINITIONS: Final[tuple[EventDefinition, ...]] = (
    _event(
        "dcc.launch",
        "DCC launch attempt terminal event.",
        "software.baseclass",
        required_payload_fields=("command_basename", "arg_count", "env_keys_set"),
        status_values=(STATUS_INFO, STATUS_SUCCESS, STATUS_ERROR),
        error_codes=(ERROR_DCC_LAUNCH_FAILED,),
    ),
    _event(
        "publish.asset.usd",
        "Asset USD publish terminal event.",
        "pipe.m.publish.publisher",
        required_payload_fields=("publish_type", "publish_path"),
        required_metrics_fields=("duration_ms",),
        error_codes=(
            ERROR_PUBLISH_PRECHECK_FAILED,
            ERROR_USD_EXPORT_FAILED,
            ERROR_PUBLISH_COPY_FAILED,
            ERROR_WINDOWS_MOVE_FAILED,
        ),
    ),
    _event(
        "publish.anim.usd",
        "Animation USD publish terminal event.",
        "pipe.m.publish.publisher",
        required_payload_fields=("publish_type", "publish_path"),
        required_metrics_fields=("duration_ms",),
        error_codes=(
            ERROR_PUBLISH_PRECHECK_FAILED,
            ERROR_USD_EXPORT_FAILED,
            ERROR_PUBLISH_COPY_FAILED,
            ERROR_WINDOWS_MOVE_FAILED,
        ),
    ),
    _event(
        "publish.camera.usd",
        "Camera USD publish terminal event.",
        "pipe.m.publish.publisher",
        required_payload_fields=("publish_type", "publish_path"),
        required_metrics_fields=("duration_ms",),
        error_codes=(
            ERROR_PUBLISH_PRECHECK_FAILED,
            ERROR_USD_EXPORT_FAILED,
            ERROR_PUBLISH_COPY_FAILED,
            ERROR_WINDOWS_MOVE_FAILED,
        ),
    ),
    _event(
        "publish.customanim.usd",
        "Custom animation USD publish terminal event.",
        "pipe.m.publish.publisher",
        required_payload_fields=("publish_type", "publish_path"),
        required_metrics_fields=("duration_ms",),
        error_codes=(
            ERROR_PUBLISH_PRECHECK_FAILED,
            ERROR_USD_EXPORT_FAILED,
            ERROR_PUBLISH_COPY_FAILED,
            ERROR_WINDOWS_MOVE_FAILED,
        ),
    ),
    _event(
        "publish.previs_asset.usd",
        "Previs asset USD publish terminal event.",
        "pipe.m.publish.publisher",
        required_payload_fields=("publish_type", "publish_path"),
        required_metrics_fields=("duration_ms",),
        error_codes=(
            ERROR_PUBLISH_PRECHECK_FAILED,
            ERROR_USD_EXPORT_FAILED,
            ERROR_PUBLISH_COPY_FAILED,
            ERROR_WINDOWS_MOVE_FAILED,
        ),
    ),
    _event(
        "build.houdini.component",
        "Houdini component build terminal event.",
        "pipe.h.assetbuilder",
        required_payload_fields=(
            "mode",
            "variant",
            "warnings_count",
            "errors_count",
        ),
        required_metrics_fields=("duration_ms",),
        error_codes=(
            ERROR_HOUDINI_BUILD_FAILED,
            ERROR_HOUDINI_BUILD_RESULT_PARSE_FAILED,
        ),
    ),
    _event(
        "texture.export.substance",
        "Substance texture export terminal event.",
        "pipe.sp.export",
        required_payload_fields=(
            "asset",
            "geo_variant",
            "material_variant",
            "renderman_variant",
            "texture_set_count",
            "udim_set_count",
        ),
        error_codes=(ERROR_TEXTURE_EXPORT_FAILED,),
    ),
    _event(
        "texture.convert.tex",
        "Texture conversion terminal event.",
        "pipe.texconverter",
        required_payload_fields=(
            "source_count",
            "converted_tex_count",
            "converted_preview_count",
            "batch_size",
        ),
        error_codes=(ERROR_TEXTURE_CONVERSION_FAILED,),
    ),
    _event(
        "file.open",
        "Scene file open terminal event.",
        "pipe.util.filemanager",
        required_payload_fields=("entity_type", "entity_code", "path", "versioned"),
        error_codes=(ERROR_FILE_OPEN_FAILED,),
    ),
    _event(
        "file.create",
        "Scene file creation terminal event.",
        "pipe.util.filemanager",
        required_payload_fields=("entity_type", "entity_code", "path", "versioned"),
        error_codes=(ERROR_FILE_CREATE_FAILED,),
    ),
    _event(
        "shot.setup",
        "Shot setup terminal event.",
        "pipe.h.hipfile.shot",
        required_payload_fields=("entity_type", "entity_code", "path", "department"),
        error_codes=(ERROR_SHOT_SETUP_FAILED,),
    ),
    _event(
        "playblast.create",
        "Playblast creation terminal event.",
        "pipe.util.playblaster",
        required_payload_fields=(
            "preset",
            "output_count",
            "frame_start",
            "frame_end",
            "fps",
        ),
        required_metrics_fields=("duration_ms",),
        error_codes=(ERROR_PLAYBLAST_FAILED,),
    ),
    _event(
        "tractor.job.spool",
        "Tractor job submission terminal event.",
        "pipeline.lib.tractor_lops",
        required_payload_fields=(
            "job_id",
            "job_title",
            "engine_url",
            "service",
            "priority",
            "renderer",
            "frame_start",
            "frame_end",
            "frame_step",
            "tile_count",
        ),
        error_codes=(ERROR_TRACTOR_SPOOL_FAILED,),
    ),
    _event(
        "tractor.farm.snapshot",
        "Periodic Tractor farm pressure snapshot.",
        "pipe.telemetry.tractor_poll",
        required_payload_fields=(
            "engine_url",
            "waiting_jobs",
            "running_jobs",
            "busy_slots",
            "total_slots",
            "active_blades",
            "total_blades",
        ),
        status_values=(STATUS_INFO, STATUS_ERROR),
        error_codes=(ERROR_TRACTOR_SNAPSHOT_FAILED,),
    ),
    _event(
        "render.stats.summary",
        "Render diagnostics summary event.",
        "pipe.telemetry.render_harvest",
        required_payload_fields=(
            "job_id",
            "renderer",
            "service",
            "total_frames",
            "failed_frames",
            "frame_time_p50_ms",
            "frame_time_p90_ms",
            "memory_peak_gb",
            "retry_count_total",
            "queue_wait_ms",
        ),
        error_codes=(ERROR_RENDER_STATS_HARVEST_FAILED,),
    ),
    _event(
        "storage.scan.summary",
        "Storage scan run summary event.",
        "pipe.telemetry.storage_scan",
        required_payload_fields=(
            "scan_id",
            "scan_window_start_utc",
            "scan_window_end_utc",
            "roots_scanned_count",
            "buckets_emitted_count",
            "scan_duration_ms",
        ),
        required_metrics_fields=(
            "size_bytes_total",
            "file_count_total",
            "dir_count_total",
        ),
        error_codes=(ERROR_STORAGE_SCAN_FAILED,),
    ),
    _event(
        "storage.scan.bucket",
        "Aggregated storage bucket usage event.",
        "pipe.telemetry.storage_scan",
        required_payload_fields=(
            "bucket_id",
            "category",
            "path",
            "scan_window_start_utc",
            "scan_window_end_utc",
            "scope_type",
            "scope_code",
        ),
        required_metrics_fields=("size_bytes", "file_count", "dir_count"),
        status_values=(STATUS_INFO,),
        error_codes=(ERROR_STORAGE_CLASSIFICATION_FAILED,),
    ),
)


def _validate_unique_fields(
    label: str, values: tuple[str, ...], event_type: str
) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"Duplicate {label} for event '{event_type}'")


def _validate_definitions(definitions: tuple[EventDefinition, ...]) -> None:
    if not definitions:
        raise ValueError(
            "Telemetry registry must declare at least one event definition"
        )

    seen_event_types: set[str] = set()
    known_error_codes = set(ERROR_CODES)

    for definition in definitions:
        event_type = definition.event_type

        if event_type in seen_event_types:
            raise ValueError(f"Duplicate telemetry event type '{event_type}'")
        seen_event_types.add(event_type)

        if not EVENT_TYPE_PATTERN.match(event_type):
            raise ValueError(
                f"Event type '{event_type}' does not match pattern "
                "'<domain>.<subject>.<action>'"
            )

        if not definition.description.strip():
            raise ValueError(f"Event '{event_type}' must have a non-empty description")

        if not definition.owner_module.strip():
            raise ValueError(f"Event '{event_type}' must define owner_module")

        if not definition.status_values:
            raise ValueError(
                f"Event '{event_type}' must define at least one status value"
            )

        if len(set(definition.status_values)) != len(definition.status_values):
            raise ValueError(f"Event '{event_type}' has duplicate status values")

        for status_value in definition.status_values:
            if status_value not in STATUS_VALUES:
                raise ValueError(
                    f"Event '{event_type}' has unknown status value '{status_value}'"
                )

        _validate_unique_fields(
            "required payload fields",
            definition.required_payload_fields,
            event_type,
        )
        _validate_unique_fields(
            "required metrics fields",
            definition.required_metrics_fields,
            event_type,
        )
        _validate_unique_fields("error codes", definition.error_codes, event_type)

        if definition.sample_rate <= 0.0 or definition.sample_rate > 1.0:
            raise ValueError(
                f"Event '{event_type}' sample_rate must be in (0, 1], "
                f"got {definition.sample_rate}"
            )

        for error_code in definition.error_codes:
            if error_code not in known_error_codes:
                raise ValueError(
                    f"Event '{event_type}' references unknown error code '{error_code}'"
                )


_validate_definitions(_EVENT_DEFINITIONS)

EVENT_DEFINITIONS: Final[tuple[EventDefinition, ...]] = _EVENT_DEFINITIONS
EVENT_TYPES: Final[tuple[str, ...]] = tuple(
    definition.event_type for definition in EVENT_DEFINITIONS
)

EVENTS_BY_TYPE: Final[dict[str, EventDefinition]] = {
    definition.event_type: definition for definition in EVENT_DEFINITIONS
}


def list_event_definitions() -> tuple[EventDefinition, ...]:
    """Return the full event contract definition set."""

    return EVENT_DEFINITIONS


def list_event_types() -> tuple[str, ...]:
    """Return all declared telemetry event type names."""

    return EVENT_TYPES


def list_error_codes() -> tuple[str, ...]:
    """Return all stable telemetry error codes."""

    return ERROR_CODES


def get_event_definition(event_type: str) -> EventDefinition:
    """Return the canonical definition for a telemetry event type."""

    try:
        return EVENTS_BY_TYPE[event_type]
    except KeyError as exc:
        raise KeyError(f"Unknown telemetry event type '{event_type}'") from exc


def is_known_event_type(event_type: str) -> bool:
    """Return True if event_type exists in the registry."""

    return event_type in EVENTS_BY_TYPE


__all__ = [
    "SCHEMA_VERSION",
    "StatusValue",
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "STATUS_WARNING",
    "STATUS_INFO",
    "STATUS_VALUES",
    "TERMINAL_STATUS_VALUES",
    "ERROR_CODES",
    "EventDefinition",
    "EVENT_DEFINITIONS",
    "EVENT_TYPES",
    "EVENTS_BY_TYPE",
    "list_event_definitions",
    "list_event_types",
    "list_error_codes",
    "get_event_definition",
    "is_known_event_type",
]
