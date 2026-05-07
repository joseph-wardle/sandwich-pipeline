"""Telemetry event registry — the event types this pipeline emits.


To add an event: one entry in `EVENT_DEFINITIONS` and one `EVENT_*` constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Status = Literal["success", "error"]

STATUS_SUCCESS: Final[Status] = "success"
STATUS_ERROR: Final[Status] = "error"

WORKFLOW_STATUSES: Final[tuple[Status, ...]] = (STATUS_SUCCESS, STATUS_ERROR)

EVENT_DCC_LAUNCH: Final[str] = "dcc.launch"
EVENT_PUBLISH_USD: Final[str] = "publish.usd"
EVENT_BUILD_HOUDINI_COMPONENT: Final[str] = "build.houdini.component"
EVENT_TEXTURE_EXPORT_SUBSTANCE: Final[str] = "texture.export.substance"
EVENT_TEXTURE_CONVERT_TEX: Final[str] = "texture.convert.tex"
EVENT_PLAYBLAST_CREATE: Final[str] = "playblast.create"


@dataclass(frozen=True)
class EventDefinition:
    """One event type's contract.

    `required_payload_fields` lists keys that must be present in the payload.
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
        required_payload_fields=("texture_set_count",),
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
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "EVENT_DCC_LAUNCH",
    "EVENT_PUBLISH_USD",
    "EVENT_BUILD_HOUDINI_COMPONENT",
    "EVENT_TEXTURE_EXPORT_SUBSTANCE",
    "EVENT_TEXTURE_CONVERT_TEX",
    "EVENT_PLAYBLAST_CREATE",
    "EventDefinition",
    "EVENT_DEFINITIONS",
    "EVENTS_BY_TYPE",
    "get_event_definition",
]
