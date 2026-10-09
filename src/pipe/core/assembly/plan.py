"""What Split Pieces would do: which group becomes which child, and what stops it.

Everything here is decided from group names, ShotGrid and disk. What only Maya
can judge (loose geometry, materials, scene units) is added by the Maya side.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from pipe.core.asset.create import create_record
from pipe.core.asset.naming import Adopt, New, Occupied, classify, name_problem
from pipe.core.asset.paths import (
    BLENDER_MODEL_FILENAME,
    MODEL_FILENAME,
    AssetPaths,
    asset_root,
)
from pipe.core.assembly.model import VARIANT_SEPARATOR, Piece, PieceTarget
from pipe.core.assembly.normalize import ScaleCheck, inspect_scale
from pipe.core.assembly.pieces import assembly_of
from pipe.core.shotgrid import Asset, ShotGrid
from pipe.core.shotgrid.paths import build_asset_path, normalize_display_name

PASTED_PREFIX = "pasted__"
_VARIANT_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


@dataclass(frozen=True)
class AddVariant:
    """The record holding the name already has files; this variant is new to it."""

    asset: Asset
    split_from: str | None


@dataclass(frozen=True)
class Child:
    """One child asset and the pieces that become its geometry variants."""

    display_name: str
    subdirectory: str | None
    claim: New | Adopt | AddVariant | Occupied
    pieces: tuple[Piece, ...]

    @property
    def asset_name(self) -> str:
        if isinstance(self.claim, (Adopt, AddVariant)):
            return self.claim.asset.name
        return normalize_display_name(self.display_name)

    @property
    def asset_path(self) -> str:
        if isinstance(self.claim, (Adopt, AddVariant)):
            return self.claim.asset.asset_path
        return build_asset_path(self.display_name, self.subdirectory)

    @property
    def variants(self) -> list[str]:
        return [piece.variant for piece in self.pieces]


@dataclass(frozen=True)
class Refusal:
    """Why a split cannot run, or stopped; `group` names the piece it concerns, if one does."""

    group: str | None
    reason: str


@dataclass(frozen=True)
class Plan:
    children: tuple[Child, ...]
    refusals: tuple[Refusal, ...]

    @property
    def pieces(self) -> list[Piece]:
        return [piece for child in self.children for piece in child.pieces]

    @property
    def ready(self) -> bool:
        return bool(self.pieces) and not self.refusals

    def refused(self, group: str) -> list[str]:
        return [r.reason for r in self.refusals if r.group == group]


def plan_split(
    pieces: Iterable[Piece],
    assembly: Asset,
    assets: Iterable[Asset],
    *,
    placed: Collection[str] = (),
    production_root: Path | None = None,
) -> Plan:
    """Decide what each of `pieces` becomes beside `assembly`, and what stops it."""
    assets = list(assets)
    by_label: dict[str, list[Piece]] = {}
    for piece in pieces:
        by_label.setdefault(piece.label, []).append(piece)

    children = []
    for label, group in by_label.items():
        display_name = display_name_for(label)
        variants = [piece.variant for piece in group]
        claim = claim_for(
            display_name, assembly.subdirectory, variants, assets, production_root
        )
        children.append(Child(display_name, assembly.subdirectory, claim, tuple(group)))

    refusals = [
        *_name_problems(children),
        *_name_collisions(children),
        *_variant_collisions(children),
        *_placed_collisions(children, placed),
        *_piece_problems(children),
    ]
    return Plan(tuple(children), tuple(refusals))


def claim_for(
    display_name: str,
    subdirectory: str | None,
    variants: Collection[str],
    assets: Iterable[Asset],
    production_root: Path | None = None,
) -> New | Adopt | AddVariant | Occupied:
    """What a split claims with `display_name`: naming's rules, except that a
    record with files takes a variant it has not built yet.

    A variant ShotGrid lists without a source layer is one a split declared and
    then failed to build."""
    assets = list(assets)
    claim = classify(display_name, subdirectory, assets, production_root)
    if not isinstance(claim, Occupied):
        return claim

    name = normalize_display_name(display_name)
    holders = [asset for asset in assets if asset.name == name]
    if len(holders) != 1:
        return claim
    (asset,) = holders

    paths = AssetPaths(asset_root(asset, production_root))
    hand_made = hand_made_model(paths.root)
    if hand_made is not None:
        return Occupied(
            f'"{asset.display_name}" is modelled by hand ({hand_made}), so a split '
            "cannot add to it. Choose a different name."
        )

    taken = [
        variant
        for variant in variants
        if paths.publish_source_variant_usd(variant).exists()
    ]
    if taken:
        return Occupied(
            f'"{asset.display_name}" already has the {", ".join(taken)} variant. '
            f"Name the group {name}{VARIANT_SEPARATOR}<variant> with a variant it "
            "does not have yet."
        )

    split_from = next(
        (
            assembly
            for layer in sorted(paths.publish_source_dir.glob("*.usd"))
            if (assembly := assembly_of(layer)) is not None
        ),
        None,
    )
    return AddVariant(asset, split_from)


def hand_made_model(asset_root: Path) -> Path | None:
    """The model file an artist made in Maya or Blender, which a split never joins."""
    return next(
        (
            path
            for path in (
                asset_root / MODEL_FILENAME,
                asset_root / BLENDER_MODEL_FILENAME,
            )
            if path.exists()
        ),
        None,
    )


def register_child(conn: ShotGrid, child: Child) -> Asset:
    """Create, adopt or extend the ShotGrid Asset for `child`, and return it."""
    claim = child.claim
    if isinstance(claim, New):
        return create_record(conn, claim, variants=child.variants)
    if isinstance(claim, Occupied):
        raise ValueError(f"'{child.display_name}' is refused: {claim.reason}")

    asset = claim.asset
    for variant in child.variants:
        asset = conn.add_geometry_variant(asset, variant)
    if isinstance(claim, Adopt):
        for stale in sorted(set(asset.geometry_variants or ()) - set(child.variants)):
            asset = conn.remove_geometry_variant(asset, stale)
    return asset


def display_name_for(label: str) -> str:
    """'conveyor_belt' -> 'Conveyor Belt', 'TiltedFrame' -> 'Tilted Frame'."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    words = [word for word in re.split(r"[\s_]+", name) if word]
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def scale_problem(piece: Piece) -> str | None:
    """Why `piece`'s scale cannot be baked into a child, or None if it can."""
    check = inspect_scale(piece.world_matrix)
    if check.bakeable:
        return None
    factors = ", ".join(f"{f:g}" for f in check.factors)
    return (
        f"'{piece.name}' has {_scale_fault(check)} (scale {factors}), which a child "
        "cannot bake. Freeze the piece's transformations, or correct its scale."
    )


def _scale_fault(check: ScaleCheck) -> str:
    if not check.positive:
        return "negative or mirrored scale"
    if not check.orthogonal:
        return "sheared axes"
    return "non-uniform scale"


def _name_problems(children: list[Child]) -> list[Refusal]:
    """A group's name must make an asset name and a variant the show can spell."""
    refusals = []
    for child in children:
        problem = name_problem(child.display_name)
        for piece in child.pieces:
            if piece.name.startswith(PASTED_PREFIX):
                refusals.append(
                    Refusal(
                        piece.name,
                        f"'{piece.name}' still carries Maya's {PASTED_PREFIX} prefix "
                        f"from a paste. Rename it {piece.name[len(PASTED_PREFIX) :]}.",
                    )
                )
                continue
            if problem is not None:
                refusals.append(
                    Refusal(
                        piece.name, f"'{piece.name}' cannot name an asset: {problem}"
                    )
                )
            if piece.variant and not _VARIANT_RE.fullmatch(piece.variant):
                refusals.append(
                    Refusal(
                        piece.name,
                        f"'{piece.name}' names the variant '{piece.variant}', and a "
                        "variant is lowercase letters, digits and single "
                        "underscores, starting with a letter (like 'main').",
                    )
                )
    return refusals


def _name_collisions(children: list[Child]) -> list[Refusal]:
    by_name: dict[str, list[Child]] = {}
    for child in children:
        by_name.setdefault(child.asset_name, []).append(child)
    refusals = []
    for name, claimants in by_name.items():
        if len(claimants) < 2:
            continue
        labels = ", ".join(f"'{child.pieces[0].label}'" for child in claimants)
        for child in claimants:
            for piece in child.pieces:
                refusals.append(
                    Refusal(
                        piece.name,
                        f"{labels} would all become the asset '{name}'. Rename all but one.",
                    )
                )
    return refusals


def _variant_collisions(children: list[Child]) -> list[Refusal]:
    refusals = []
    for child in children:
        for variant in dict.fromkeys(child.variants):
            pieces = [piece for piece in child.pieces if piece.variant == variant]
            if len(pieces) < 2:
                continue
            groups = ", ".join(f"'{piece.name}'" for piece in pieces)
            for piece in pieces:
                refusals.append(
                    Refusal(
                        piece.name,
                        f"{groups} would all be the '{variant}' variant of "
                        f"'{child.display_name}'. Rename all but one.",
                    )
                )
    return refusals


def _placed_collisions(children: list[Child], placed: Collection[str]) -> list[Refusal]:
    refusals = []
    for child in children:
        for piece in child.pieces:
            prim = PieceTarget(child.asset_name, Path(), piece.variant).prim_name
            if prim in placed:
                refusals.append(
                    Refusal(
                        piece.name,
                        f"'{piece.name}' would become '{prim}', which is already a "
                        "piece of this assembly. Name it as another variant.",
                    )
                )
    return refusals


def _piece_problems(children: list[Child]) -> list[Refusal]:
    refusals = []
    for child in children:
        if isinstance(child.claim, Occupied):
            for piece in child.pieces:
                refusals.append(
                    Refusal(
                        piece.name,
                        f"'{piece.name}' cannot become '{child.display_name}': "
                        f"{child.claim.reason}",
                    )
                )
        for piece in child.pieces:
            if not piece.variant:
                refusals.append(
                    Refusal(
                        piece.name,
                        f"'{piece.name}' names no variant after '{VARIANT_SEPARATOR}'. "
                        f"Name it <asset>{VARIANT_SEPARATOR}<variant>, or drop the "
                        "underscores.",
                    )
                )
            problem = scale_problem(piece)
            if problem is not None:
                refusals.append(Refusal(piece.name, problem))
    return refusals
