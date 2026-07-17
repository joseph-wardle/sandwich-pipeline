"""The JSON handoff a DCC writes for `pipe view`.

The spec must carry everything the DCC knew at render time — the viewer
process cannot ask the DCC anything, and never computes pipeline paths
itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import attrs
import cattrs

from pipe.core.playblast.presets import FFmpegPreset

PREVIEW_SPEC_FILENAME = "preview_spec.json"


def padded_frame_number(frame: int, width: int = 4) -> str:
    """Render `frame` as the fixed-width number used in frame filenames,
    preserving a leading `-` for negatives but emitting no sign for positives."""
    return f"{frame:+0{width + 1}d}".replace("+", "")


@attrs.frozen
class Destination:
    """One folder row in the viewer's Destinations panel: where a confirmed
    clip is copied, and the encode preset that folder expects."""

    name: str
    directory: Path
    preset: FFmpegPreset
    default_on: bool = True
    browsable: bool = False


@attrs.frozen
class ShotGridUpload:
    """The clip's ShotGrid row: the entity a confirmed Version attaches to."""

    entity_kind: Literal["shot", "asset", "scratch"]
    entity_value: str
    artist_display_name: str | None = None
    playlist_required: bool = False


@attrs.frozen
class PrevisStamp:
    """Manifest-stamp context for a previs take export. Carried by the spec
    now; the viewer starts stamping takes on Confirm in Phase 3."""

    sequence_code: str
    shot_code: str
    take_version: int
    camera: str


@attrs.frozen
class PreviewClip:
    """One playable clip in the viewer: a shot playblast, or one previs take."""

    label: str
    frames_dir: Path
    frames_basename: str
    frame_start: int
    frame_end: int
    output_prefix: str = ""
    settings_key: str = ""
    destinations: tuple[Destination, ...] = ()
    shotgrid: ShotGridUpload | None = None
    previs_stamp: PrevisStamp | None = None

    def frame_path(self, frame: int) -> Path:
        return self.frames_dir / (
            f"{self.frames_basename}.{padded_frame_number(frame)}.png"
        )


@attrs.frozen
class PreviewSpec:
    fps: int
    resolution: tuple[int, int]
    clips: list[PreviewClip]


class PreviewSpecError(Exception):
    """A preview spec file is missing or malformed."""


def save_preview_spec(spec: PreviewSpec, path: Path) -> None:
    path.write_text(json.dumps(_converter.unstructure(spec), indent=2))


def load_preview_spec(path: Path) -> PreviewSpec:
    try:
        return _converter.structure(json.loads(path.read_text()), PreviewSpec)
    except FileNotFoundError as exc:
        raise PreviewSpecError(f"Preview spec does not exist: {path}") from exc
    except Exception as exc:
        raise PreviewSpecError(f"Could not parse preview spec {path}: {exc}") from exc


def _preset_to_name(preset: FFmpegPreset) -> str:
    return preset.name


def _preset_from_name(name: str, _: type) -> FFmpegPreset:
    return FFmpegPreset[name]


# Presets serialize by name ("WEB")
_converter = cattrs.Converter()
_converter.register_unstructure_hook(FFmpegPreset, _preset_to_name)
_converter.register_structure_hook(FFmpegPreset, _preset_from_name)


__all__ = [
    "PREVIEW_SPEC_FILENAME",
    "Destination",
    "PrevisStamp",
    "PreviewClip",
    "PreviewSpec",
    "PreviewSpecError",
    "ShotGridUpload",
    "load_preview_spec",
    "padded_frame_number",
    "save_preview_spec",
]
