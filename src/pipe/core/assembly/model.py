"""The data a split moves between the Maya scene, the child asset, and the stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pxr import Gf

from pipe.core.asset.paths import DEFAULT_GEOMETRY_VARIANT, AssetPaths

# A top-level group named `<asset>__<variant>` is one geometry variant of that
# asset; a plain `<asset>` group is its main variant.
VARIANT_SEPARATOR = "__"


class AssemblyError(Exception):
    """Something an artist must fix. The message is written for them to act on."""


class SplitError(AssemblyError):
    """A piece cannot be split."""


class EditError(AssemblyError):
    """A piece cannot be opened for editing, or its edits cannot be saved back."""


def piece_name_parts(name: str) -> tuple[str, str]:
    """'frame__tall' -> ('frame', 'tall'); 'frame' -> ('frame', 'main')."""
    label, separator, variant = name.partition(VARIANT_SEPARATOR)
    return label, variant if separator else DEFAULT_GEOMETRY_VARIANT


@dataclass(frozen=True)
class Piece:
    """A top-level Maya group destined to become a child asset."""

    node: str
    world_matrix: Gf.Matrix4d

    @property
    def name(self) -> str:
        return self.node.rsplit("|", 1)[-1]

    @property
    def label(self) -> str:
        """The asset part of the name: 'frame' for 'frame__tall'."""
        return piece_name_parts(self.name)[0]

    @property
    def variant(self) -> str:
        return piece_name_parts(self.name)[1]


@dataclass(frozen=True)
class PieceTarget:
    """The child asset a piece is being split into."""

    asset_name: str
    asset_root: Path
    variant: str = DEFAULT_GEOMETRY_VARIANT

    @property
    def source_layer(self) -> Path:
        return AssetPaths(self.asset_root).publish_source_variant_usd(self.variant)

    @property
    def prim_name(self) -> str:
        """The piece prim's name, and the child layer's default prim: they must agree.

        *Merge Maya Edits to USD* writes a pulled piece back under its Maya name,
        which is the prim's, so a child layer whose root is named otherwise would
        gain a second root instead of being edited.
        """
        if self.variant == DEFAULT_GEOMETRY_VARIANT:
            return self.asset_name
        return f"{self.asset_name}{VARIANT_SEPARATOR}{self.variant}"


@dataclass(frozen=True)
class SplitResult:
    """What a split produced: the piece, and the child and prim it became."""

    piece_name: str
    asset_name: str
    variant: str
    prim_path: str
