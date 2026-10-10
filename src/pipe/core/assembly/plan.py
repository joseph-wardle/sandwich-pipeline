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
    production_relative_identifier,
)
from pipe.core.assembly.model import VARIANT_SEPARATOR, Piece, piece_prim_name
from pipe.core.assembly.normalize import inspect_scale
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
    def variants(self) -> set[str]:
        return {piece.variant for piece in self.pieces}


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


def plan_children(
    pieces: Iterable[Piece],
    assembly: Asset,
    assets: Iterable[Asset],
    *,
    placed: Collection[str] = (),
    production_root: Path | None = None,
) -> Plan:
    """Decide what each of `pieces` becomes beside `assembly`, and what stops it."""
    assets = list(assets)
    assembly_root = asset_root(assembly, production_root)
    by_label: dict[str, list[Piece]] = {}
    for piece in pieces:
        by_label.setdefault(piece.label, []).append(piece)

    children = []
    for label, group in by_label.items():
        display_name = display_name_for(label)
        claim = claim_for(
            display_name,
            assembly.subdirectory,
            {piece.variant for piece in group},
            assets,
            production_root,
            assembly_root=assembly_root,
            placed=placed,
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
    *,
    assembly_root: Path,
    placed: Collection[str] = (),
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
    problem = existing_model_problem(
        paths.root, name, variants, assembly_root=assembly_root, placed=placed
    )
    if problem is not None:
        return Occupied(problem)

    split_from = next(
        (
            assembly
            for layer in sorted(paths.publish_source_dir.glob("*.usd"))
            if (assembly := assembly_of(layer)) is not None
        ),
        None,
    )
    return AddVariant(asset, split_from)


def existing_model_problem(
    child_root: Path,
    asset_name: str,
    variants: Collection[str],
    *,
    assembly_root: Path,
    placed: Collection[str],
) -> str | None:
    """Why a split cannot write `variants` into the child at `child_root`, or None."""
    hand_made = hand_made_model(child_root)
    if hand_made is not None:
        return (
            f"'{asset_name}' is modelled by hand ({hand_made}), so a split cannot "
            "add to it. Choose a different name."
        )

    paths = AssetPaths(child_root)
    this_assembly = production_relative_identifier(assembly_root)
    taken: list[str] = []
    orphaned: list[str] = []
    for variant in sorted(variants):
        source = paths.publish_source_variant_usd(variant)
        if not source.exists():
            continue
        if (
            assembly_of(source) == this_assembly
            and piece_prim_name(asset_name, variant) not in placed
        ):
            orphaned.append(str(source))
        else:
            taken.append(variant)
    if orphaned:
        return (
            f"'{asset_name}' has a layer from an interrupted split of this assembly "
            f"that no piece uses: {', '.join(orphaned)}. Delete the file, then "
            "refresh the plan and split again."
        )
    if taken:
        return (
            f"'{asset_name}' already has the {', '.join(taken)} variant. Name the "
            f"group {asset_name}{VARIANT_SEPARATOR}<variant> with a variant it does "
            "not have yet."
        )
    return None


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
        return create_record(conn, claim, variants=sorted(child.variants))
    if isinstance(claim, Occupied):
        raise ValueError(f"'{child.display_name}' is refused: {claim.reason}")

    asset = claim.asset
    for variant in sorted(child.variants):
        asset = conn.add_geometry_variant(asset, variant)
    sources = AssetPaths(asset_root(asset))
    for stale in sorted(set(asset.geometry_variants or ()) - child.variants):
        if (
            isinstance(claim, Adopt)
            or not sources.publish_source_variant_usd(stale).exists()
        ):
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
    advice = "Freeze the piece's transformations, or correct its scale."
    if not check.positive:
        # The factors are axis lengths, which never show the sign that is wrong.
        return (
            f"'{piece.name}' is mirrored or squashed flat (a negative or zero "
            f"scale), which a child cannot bake. {advice}"
        )
    fault = "non-uniform scale" if check.orthogonal else "sheared axes"
    factors = ", ".join(f"{f:g}" for f in check.factors)
    return (
        f"'{piece.name}' has {fault} (scale {factors}), which a child cannot "
        f"bake. {advice}"
    )


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
            if not _VARIANT_RE.fullmatch(piece.variant):
                refusals.append(
                    Refusal(
                        piece.name,
                        f"'{piece.name}' needs a variant after '{VARIANT_SEPARATOR}' "
                        "of lowercase letters, digits and single underscores, "
                        f"starting with a letter (like '{piece.label}"
                        f"{VARIANT_SEPARATOR}tall'), or no '{VARIANT_SEPARATOR}' "
                        "at all for the main variant.",
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
            prim = piece_prim_name(child.asset_name, piece.variant)
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
            problem = scale_problem(piece)
            if problem is not None:
                refusals.append(Refusal(piece.name, problem))
    return refusals
