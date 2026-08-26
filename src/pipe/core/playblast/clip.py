"""What a playblast hands the viewer at launch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, NewType

import attrs

from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.playblast.tempdir import resolve_playblast_tempdir


def padded_frame_number(frame: int, width: int = 4) -> str:
    return f"{frame:+0{width + 1}d}".replace("+", "")


DestinationId = NewType("DestinationId", str)

EDIT_FOLDER_ID = DestinationId("edit")
EDIT_FOLDER_NAME = "Send to Edit"
CURRENT_FOLDER_ID = DestinationId("current_folder")
CURRENT_FOLDER_NAME = "Current Folder"
CUSTOM_FOLDER_ID = DestinationId("custom_folder")
CUSTOM_FOLDER_NAME = "Custom Folder"
RENDER_FOLDER_ID = DestinationId("render_folder")
PREVIS_FOLDER_ID = DestinationId("previs_folder")


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
    """A scene with no pipeline entity to attach to."""

    label: str

    @property
    def description(self) -> str:
        return f"scratch scene '{self.label}'"


ReviewEntity = ShotEntity | AssetEntity | ScratchEntity


def is_unlinked(entity: ReviewEntity) -> bool:
    """Whether the Version hangs off the project alone."""
    return isinstance(entity, ScratchEntity)


def shot_or_scratch(shot_code: str, scratch_label: str) -> ReviewEntity:
    """A scene with no pipeline shot still uploads, unlinked."""
    code = shot_code.strip()
    return ShotEntity(code) if code else ScratchEntity(scratch_label.strip())


@attrs.frozen
class DiskDestination:
    """`unavailable`, when set, is why this row cannot be delivered to yet."""

    id: DestinationId
    name: str
    directory: Path
    preset: FFmpegPreset
    default_on: bool = True
    browsable: bool = False
    unavailable: str = ""


@attrs.frozen
class ShotGridDestination:
    """A ShotGrid Version, optionally linked to a review playlist."""

    id: ClassVar[DestinationId] = DestinationId("shotgrid")
    name: ClassVar[str] = "ShotGrid"
    # Always deliverable, so every `Destination` answers the same question.
    unavailable: ClassVar[str] = ""
    # ShotGrid transcodes whatever it receives, so the upload reuses the WEB
    # encode a checked WEB folder row already needs.
    preset: ClassVar[FFmpegPreset] = FFmpegPreset.WEB

    entity: ReviewEntity
    default_on: bool = True

    @property
    def playlist_required(self) -> bool:
        return is_unlinked(self.entity)


Destination = DiskDestination | ShotGridDestination


def custom_folder_destination(*, default_on: bool = False) -> DiskDestination:
    return DiskDestination(
        id=CUSTOM_FOLDER_ID,
        name=CUSTOM_FOLDER_NAME,
        directory=resolve_playblast_tempdir(),
        preset=FFmpegPreset.WEB,
        default_on=default_on,
        browsable=True,
    )


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
    "CURRENT_FOLDER_NAME",
    "CUSTOM_FOLDER_ID",
    "CUSTOM_FOLDER_NAME",
    "EDIT_FOLDER_ID",
    "EDIT_FOLDER_NAME",
    "PREVIS_FOLDER_ID",
    "RENDER_FOLDER_ID",
    "AssetEntity",
    "Destination",
    "DestinationId",
    "DiskDestination",
    "PreviewClip",
    "ReviewEntity",
    "ScratchEntity",
    "ShotEntity",
    "ShotGridDestination",
    "custom_folder_destination",
    "is_unlinked",
    "padded_frame_number",
]
