"""What a playblast hands the viewer at launch."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import attrs

from pipe.core.playblast.presets import FFmpegPreset


def padded_frame_number(frame: int, width: int = 4) -> str:
    return f"{frame:+0{width + 1}d}".replace("+", "")


@attrs.frozen
class Destination:
    name: str
    directory: Path
    preset: FFmpegPreset
    default_on: bool = True
    browsable: bool = False


@attrs.frozen
class ShotGridUpload:
    entity_kind: Literal["shot", "asset", "scratch"]
    entity_value: str
    artist_display_name: str | None = None
    playlist_required: bool = False


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
class PreviewClip:
    label: str
    frames_dir: Path
    frames_basename: str
    frame_start: int
    frame_end: int
    fps: int
    output_prefix: str = ""
    settings_key: str = ""
    destinations: tuple[Destination, ...] = ()
    shotgrid: ShotGridUpload | None = None
    previs_stamp: PrevisStamp | None = None

    def frame_path(self, frame: int) -> Path:
        return self.frames_dir / (
            f"{self.frames_basename}.{padded_frame_number(frame)}.png"
        )


__all__ = [
    "Destination",
    "PrevisStamp",
    "PreviewClip",
    "ShotGridUpload",
    "padded_frame_number",
]
