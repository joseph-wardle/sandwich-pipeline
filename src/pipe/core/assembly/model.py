"""The data a split moves between the Maya scene, the child asset, and the stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pxr import Gf

from pipe.core.asset.paths import DEFAULT_GEOMETRY_VARIANT, AssetPaths


class SplitError(Exception):
    """A piece cannot be split. The message is written for an artist to act on."""


@dataclass(frozen=True)
class Piece:
    """A top-level Maya group destined to become a child asset."""

    node: str
    world_matrix: Gf.Matrix4d

    @property
    def name(self) -> str:
        return self.node.rsplit("|", 1)[-1]


@dataclass(frozen=True)
class PieceTarget:
    """The child asset a piece is being split into."""

    asset_name: str
    asset_root: Path
    variant: str = DEFAULT_GEOMETRY_VARIANT

    @property
    def source_layer(self) -> Path:
        return AssetPaths(self.asset_root).publish_source_variant_usd(self.variant)


@dataclass(frozen=True)
class SplitResult:
    """What a split produced.

    The two bounds are the evidence `split_piece` checked before it deleted
    anything; they are kept so the tool can show an artist what it measured.
    """

    piece_name: str
    asset_name: str
    source_layer: Path
    prim_path: str
    placement: Gf.Matrix4d
    world_bounds_before: Gf.Range3d
    world_bounds_after: Gf.Range3d
