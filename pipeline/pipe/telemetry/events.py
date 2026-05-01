"""Telemetry event registry — the 10 event types this pipeline emits.

The registry is the single source of truth for which events exist and what
fields each one's `payload` is expected to contain. The `action` context
manager and the bare `emit` function use it for validation; the ingester
uses it on the read side; Grafana dashboards reference these names.

Adding a new event type is one entry in `EVENT_DEFINITIONS` and one
`EVENT_*` constant. Adding a new payload field is editing one tuple.

Two events are info-only (snapshots that aren't workflow-shaped):
`tractor.farm.snapshot` and `storage.scan.bucket`. The rest follow the
workflow lifecycle (success or error).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Status = Literal["info", "success", "error"]

STATUS_INFO: Final[Status] = "info"
STATUS_SUCCESS: Final[Status] = "success"
STATUS_ERROR: Final[Status] = "error"

WORKFLOW_STATUSES: Final[tuple[Status, ...]] = (STATUS_SUCCESS, STATUS_ERROR)
INFO_ONLY_STATUSES: Final[tuple[Status, ...]] = (STATUS_INFO,)

EVENT_DCC_LAUNCH: Final[str] = "dcc.launch"
EVENT_PUBLISH_USD: Final[str] = "publish.usd"
EVENT_BUILD_HOUDINI_COMPONENT: Final[str] = "build.houdini.component"
EVENT_TEXTURE_EXPORT_SUBSTANCE: Final[str] = "texture.export.substance"
EVENT_TEXTURE_CONVERT_TEX: Final[str] = "texture.convert.tex"
EVENT_PLAYBLAST_CREATE: Final[str] = "playblast.create"
EVENT_TRACTOR_FARM_SNAPSHOT: Final[str] = "tractor.farm.snapshot"
EVENT_RENDER_STATS_SUMMARY: Final[str] = "render.stats.summary"
EVENT_STORAGE_SCAN_SUMMARY: Final[str] = "storage.scan.summary"
EVENT_STORAGE_SCAN_BUCKET: Final[str] = "storage.scan.bucket"


@dataclass(frozen=True)
class EventDefinition:
    """One event type's contract.

    `required_payload_fields` lists keys that must be present in the payload.
    Strict mode (PIPE_TELEMETRY_STRICT=1, used in CI) raises on missing
    fields; lenient mode (production default) drops the event with a warning.
    """

    event_type: str
    description: str
    required_payload_fields: tuple[str, ...] = ()
    statuses: tuple[Status, ...] = WORKFLOW_STATUSES
    has_duration: bool = False


EVENT_DEFINITIONS: Final[tuple[EventDefinition, ...]] = (
    EventDefinition(
        event_type=EVENT_DCC_LAUNCH,
        description="DCC (Maya, Houdini, Nuke, Substance Painter) launch attempt.",
        required_payload_fields=("command_basename",),
    ),
    EventDefinition(
        event_type=EVENT_PUBLISH_USD,
        description=(
            "USD publish terminal event. The `kind` payload field discriminates "
            "asset / anim / camera / customanim / previs_asset publishes."
        ),
        required_payload_fields=("kind", "publish_path"),
        has_duration=True,
    ),
    EventDefinition(
        event_type=EVENT_BUILD_HOUDINI_COMPONENT,
        description="Houdini headless component build terminal event.",
        required_payload_fields=("mode", "variant"),
        has_duration=True,
    ),
    EventDefinition(
        event_type=EVENT_TEXTURE_EXPORT_SUBSTANCE,
        description="Substance Painter texture export terminal event.",
        required_payload_fields=("asset", "texture_set_count"),
        has_duration=True,
    ),
    EventDefinition(
        event_type=EVENT_TEXTURE_CONVERT_TEX,
        description="Texture conversion (tex / txmake) terminal event.",
        required_payload_fields=("source_count", "converted_tex_count"),
        has_duration=True,
    ),
    EventDefinition(
        event_type=EVENT_PLAYBLAST_CREATE,
        description="Playblast creation terminal event.",
        required_payload_fields=("preset", "frame_start", "frame_end", "fps"),
        has_duration=True,
    ),
    EventDefinition(
        event_type=EVENT_TRACTOR_FARM_SNAPSHOT,
        description="Periodic Tractor farm pressure snapshot.",
        required_payload_fields=(
            "engine_url",
            "waiting_jobs",
            "running_jobs",
            "busy_slots",
            "total_slots",
            "active_blades",
            "total_blades",
        ),
        statuses=INFO_ONLY_STATUSES,
    ),
    EventDefinition(
        event_type=EVENT_RENDER_STATS_SUMMARY,
        description="Per-render-job statistics harvested from Tractor artifacts.",
        required_payload_fields=(
            "job_id",
            "renderer",
            "total_frames",
            "failed_frames",
        ),
    ),
    EventDefinition(
        event_type=EVENT_STORAGE_SCAN_SUMMARY,
        description="Run-summary event for one storage scan pass.",
        required_payload_fields=(
            "scan_id",
            "roots_scanned_count",
            "buckets_emitted_count",
        ),
        has_duration=True,
    ),
    EventDefinition(
        event_type=EVENT_STORAGE_SCAN_BUCKET,
        description=(
            "One aggregated storage bucket. `is_reclaimable` and `reason` "
            "express whether the storage scanner classified this bucket as "
            "safe to delete."
        ),
        required_payload_fields=(
            "bucket_id",
            "category",
            "path",
            "size_bytes",
            "file_count",
        ),
        statuses=INFO_ONLY_STATUSES,
    ),
)


EVENTS_BY_TYPE: Final[dict[str, EventDefinition]] = {
    definition.event_type: definition for definition in EVENT_DEFINITIONS
}


def get_event_definition(event_type: str) -> EventDefinition:
    """Return the registry entry for `event_type`. Raises KeyError if unknown."""

    try:
        return EVENTS_BY_TYPE[event_type]
    except KeyError as exc:
        raise KeyError(
            f"Unknown telemetry event type {event_type!r}. "
            f"Known events: {sorted(EVENTS_BY_TYPE)}"
        ) from exc


__all__ = [
    "Status",
    "STATUS_INFO",
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "EVENT_DCC_LAUNCH",
    "EVENT_PUBLISH_USD",
    "EVENT_BUILD_HOUDINI_COMPONENT",
    "EVENT_TEXTURE_EXPORT_SUBSTANCE",
    "EVENT_TEXTURE_CONVERT_TEX",
    "EVENT_PLAYBLAST_CREATE",
    "EVENT_TRACTOR_FARM_SNAPSHOT",
    "EVENT_RENDER_STATS_SUMMARY",
    "EVENT_STORAGE_SCAN_SUMMARY",
    "EVENT_STORAGE_SCAN_BUCKET",
    "EventDefinition",
    "EVENT_DEFINITIONS",
    "EVENTS_BY_TYPE",
    "get_event_definition",
]
