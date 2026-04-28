from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pipe.playblast import FFmpegPreset
from pipe.shotgrid import Shot

DEFAULT_RESOLUTION = (1280, 720)


@dataclass(frozen=True)
class ResolvedOutputDestination:
    """Resolved output base path paired with its destination label."""

    destination_name: str
    output_base: Path


@dataclass(frozen=True)
class HoudiniPlayblastLaunchContext:
    """Resolved inputs used by the Houdini playblast launch flow."""

    source_mode: Literal["shot", "custom"]
    shot_code: str | None
    custom_camera_path: str | None
    custom_frame_range: tuple[int, int] | None
    custom_shot_code: str
    output_destinations: tuple[ResolvedOutputDestination, ...]
    shotgrid_description: str
    upload_to_shotgrid: bool
    shotgrid_upload_target: str
    shotgrid_review_playlist_id: int | None
    shotgrid_review_load_error: str | None


@dataclass(frozen=True)
class HoudiniPlayblastExportConfig:
    """Fully resolved export configuration used by launch orchestration."""

    context: HoudiniPlayblastLaunchContext
    shot: Shot
    out_paths: dict[FFmpegPreset, list[Path | str]]
    final_movies: tuple[Path, ...]


__all__ = [
    "DEFAULT_RESOLUTION",
    "HoudiniPlayblastExportConfig",
    "HoudiniPlayblastLaunchContext",
    "ResolvedOutputDestination",
]
