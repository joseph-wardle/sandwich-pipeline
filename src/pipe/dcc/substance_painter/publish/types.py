"""Shared data structures for Substance Painter texture export."""

from __future__ import annotations

from dataclasses import dataclass

import substance_painter as sp

from pipe.core.struct.material import DisplacementSource, NormalSource


@dataclass
class TexSetExportSettings:
    tex_set: sp.textureset.TextureSet
    extra_channels: set[sp.textureset.Channel]
    resolution: int
    displacement_source: DisplacementSource
    normal_source: NormalSource


@dataclass(frozen=True)
class ResolvedExportTarget:
    settings: TexSetExportSettings
    stack: sp.textureset.Stack
    texture_set_name: str
