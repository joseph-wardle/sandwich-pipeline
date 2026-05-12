"""Shared data structures for Substance Painter texture export."""

from __future__ import annotations

from dataclasses import dataclass

import substance_painter as sp

from core.struct.material import DisplacementSource, NormalSource, NormalType


@dataclass
class TexSetExportSettings:
    tex_set: sp.textureset.TextureSet
    extra_channels: set[sp.textureset.Channel]
    resolution: int
    displacement_source: DisplacementSource
    normal_type: NormalType
    normal_source: NormalSource


@dataclass(frozen=True)
class ResolvedExportTarget:
    settings: TexSetExportSettings
    stack: sp.textureset.Stack
    texture_set_name: str


@dataclass
class ExportEventSnapshot:
    about_to_start_textures: dict[tuple[str, str], list[str]] | None = None
    ended_status: sp.export.ExportStatus | None = None
    ended_message: str | None = None
    ended_textures: dict[tuple[str, str], list[str]] | None = None


@dataclass(frozen=True)
class TargetExportOutcome:
    planned_exports: dict[tuple[str, str], list[str]]
    exported_textures: dict[tuple[str, str], list[str]]
    returned_texture_count: int
    event_texture_count: int
    event_planned_texture_count: int
    used_event_fallback: bool
