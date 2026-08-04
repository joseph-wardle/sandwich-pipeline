"""What a playblast hands the viewer at launch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, NewType

import attrs

from pipe.core.playblast.presets import FFmpegPreset


def padded_frame_number(frame: int, width: int = 4) -> str:
    return f"{frame:+0{width + 1}d}".replace("+", "")


DestinationId = NewType("DestinationId", str)

EDIT_FOLDER_ID = DestinationId("edit")
CURRENT_FOLDER_ID = DestinationId("current_folder")
CUSTOM_FOLDER_ID = DestinationId("custom_folder")
RENDER_FOLDER_ID = DestinationId("render_folder")


@attrs.frozen
class ShotEntity:
    code: str

    @property
    def description(self) -> str:
        return f"shot {self.code}"


@attrs.frozen
class AssetEntity:
    display_name: str

    @property
    def description(self) -> str:
        return f"asset {self.display_name}"


@attrs.frozen
class ScratchEntity:
    """A scene with no pipeline entity to attach to. Its Version is created at
    the project level, so it is discoverable only inside a review playlist."""

    label: str

    @property
    def description(self) -> str:
        return f"scratch scene '{self.label}'"


ReviewEntity = ShotEntity | AssetEntity | ScratchEntity


@attrs.frozen
class PrevisStamp:
    """Manifest-stamp context for a previs take."""

    sequence_code: str
    shot_code: str
    camera: str
    source_filename: str
    duration_frames: int
    previs_root: Path


@attrs.frozen
class DiskDestination:
    id: DestinationId
    name: str
    directory: Path
    preset: FFmpegPreset
    default_on: bool = True
    browsable: bool = False


@attrs.frozen
class ShotGridDestination:
    """A ShotGrid Version, optionally linked to a review playlist."""

    id: ClassVar[DestinationId] = DestinationId("shotgrid")
    name: ClassVar[str] = "ShotGrid"
    # ShotGrid transcodes whatever it receives, so the upload reuses the WEB
    # encode a checked WEB folder row already needs.
    preset: ClassVar[FFmpegPreset] = FFmpegPreset.WEB

    entity: ReviewEntity
    artist_display_name: str | None = None
    playlist_required: bool = False
    default_on: bool = False


@attrs.frozen
class PrevisTakeDestination:
    """The immutable previs take: a movie in the sequence's playblasts dir plus
    a manifest stamp."""

    id: ClassVar[DestinationId] = DestinationId("previs_take")
    name: ClassVar[str] = "Send to Edit"
    preset: ClassVar[FFmpegPreset] = FFmpegPreset.EDIT_SQ

    stamp: PrevisStamp
    default_on: bool = True


Destination = DiskDestination | ShotGridDestination | PrevisTakeDestination


def destination_rows(*rows: Destination | None) -> tuple[Destination, ...]:
    """Drop the rows a tool has no context to offer."""
    return tuple(row for row in rows if row is not None)


def _validate_destinations(
    instance: PreviewClip,
    _attribute: attrs.Attribute[Any],
    value: tuple[Destination, ...],
) -> None:
    if not value:
        return
    if not instance.output_prefix.strip():
        raise ValueError(
            "A preview clip with destinations needs an output prefix to version "
            "its filenames from."
        )
    ids = [destination.id for destination in value]
    if len(set(ids)) != len(ids):
        raise ValueError(f"Destination ids must be unique; got {', '.join(ids)}.")


@attrs.frozen
class PreviewClip:
    label: str
    frames_dir: Path
    frames_basename: str
    frame_start: int
    frame_end: int
    fps: int
    output_prefix: str = ""
    settings_key: str = ""
    destinations: tuple[Destination, ...] = attrs.field(
        default=(), validator=_validate_destinations
    )

    def frame_path(self, frame: int) -> Path:
        return self.frames_dir / (
            f"{self.frames_basename}.{padded_frame_number(frame)}.png"
        )


__all__ = [
    "CURRENT_FOLDER_ID",
    "CUSTOM_FOLDER_ID",
    "EDIT_FOLDER_ID",
    "RENDER_FOLDER_ID",
    "AssetEntity",
    "Destination",
    "DestinationId",
    "DiskDestination",
    "PrevisStamp",
    "PrevisTakeDestination",
    "PreviewClip",
    "ReviewEntity",
    "ScratchEntity",
    "ShotEntity",
    "ShotGridDestination",
    "destination_rows",
    "padded_frame_number",
]
