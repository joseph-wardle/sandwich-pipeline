"""What Split Pieces would do: which group becomes which child, and what stops it.

Everything here is decided from group names, ShotGrid and disk. What only Maya
can judge (loose geometry, materials, scene units) is added by the Maya side.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from pipe.core.asset.naming import Adopt, New, Occupied, classify, name_problem
from pipe.core.asset.paths import (
    BLENDER_MODEL_FILENAME,
    MODEL_FILENAME,
    AssetPaths,
    asset_root,
)
from pipe.core.assembly.model import (
    VARIANT_SEPARATOR,
    Piece,
    PieceTarget,
    piece_name_parts,
)
from pipe.core.assembly.normalize import ScaleCheck, inspect_scale
from pipe.core.assembly.provenance import assembly_of
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
class Row:
    """One unsplit group, and the asset and variant its name declares."""

    piece: Piece
    label: str
    variant: str

    @property
    def group(self) -> str:
        return self.piece.name


@dataclass(frozen=True)
class Child:
    """One child asset and the rows that become its geometry variants."""

    display_name: str
    subdirectory: str | None
    claim: New | Adopt | AddVariant | Occupied
    rows: tuple[Row, ...]

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
        return [row.variant for row in self.rows]


@dataclass(frozen=True)
class Refusal:
    """Why the split cannot run; `group` names the row it concerns, if one does."""

    group: str | None
    reason: str


@dataclass(frozen=True)
class Plan:
    children: tuple[Child, ...]
    refusals: tuple[Refusal, ...]

    @property
    def rows(self) -> list[Row]:
        return [row for child in self.children for row in child.rows]

    @property
    def ready(self) -> bool:
        return bool(self.rows) and not self.refusals

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
    by_label: dict[str, list[Row]] = {}
    for piece in pieces:
        label, variant = piece_name_parts(piece.name.removeprefix(PASTED_PREFIX))
        by_label.setdefault(label, []).append(Row(piece, label, variant))

    children = []
    for label, rows in by_label.items():
        display_name = display_name_for(label)
        variants = [row.variant for row in rows]
        claim = claim_for(
            display_name, assembly.subdirectory, variants, assets, production_root
        )
        children.append(Child(display_name, assembly.subdirectory, claim, tuple(rows)))

    refusals = [
        *_name_problems(children),
        *_name_collisions(children),
        *_variant_collisions(children),
        *_placed_collisions(children, placed),
        *_row_problems(children),
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
    hand_made = next(
        (
            path
            for path in (
                paths.root / MODEL_FILENAME,
                paths.root / BLENDER_MODEL_FILENAME,
            )
            if path.exists()
        ),
        None,
    )
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


def register_child(conn: ShotGrid, child: Child) -> Asset:
    """Create, adopt or extend the ShotGrid Asset for `child`, and return it."""
    from pipe.core.asset.create import (
        create_record,
    )

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
        for row in child.rows:
            if problem is not None:
                refusals.append(
                    Refusal(row.group, f"'{row.group}' cannot name an asset: {problem}")
                )
            if row.variant and not _VARIANT_RE.fullmatch(row.variant):
                refusals.append(
                    Refusal(
                        row.group,
                        f"'{row.group}' names the variant '{row.variant}', and a "
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
        labels = ", ".join(f"'{child.rows[0].label}'" for child in claimants)
        for child in claimants:
            for row in child.rows:
                refusals.append(
                    Refusal(
                        row.group,
                        f"{labels} would all become the asset '{name}'. Rename all but one.",
                    )
                )
    return refusals


def _variant_collisions(children: list[Child]) -> list[Refusal]:
    refusals = []
    for child in children:
        for variant in dict.fromkeys(child.variants):
            rows = [row for row in child.rows if row.variant == variant]
            if len(rows) < 2:
                continue
            groups = ", ".join(f"'{row.group}'" for row in rows)
            for row in rows:
                refusals.append(
                    Refusal(
                        row.group,
                        f"{groups} would all be the '{variant}' variant of "
                        f"'{child.display_name}'. Rename all but one.",
                    )
                )
    return refusals


def _placed_collisions(children: list[Child], placed: Collection[str]) -> list[Refusal]:
    refusals = []
    for child in children:
        for row in child.rows:
            prim = PieceTarget(child.asset_name, Path(), row.variant).prim_name
            if prim in placed:
                refusals.append(
                    Refusal(
                        row.group,
                        f"'{row.group}' would become '{prim}', which is already a "
                        "piece of this assembly. Name it as another variant.",
                    )
                )
    return refusals


def _row_problems(children: list[Child]) -> list[Refusal]:
    refusals = []
    for child in children:
        if isinstance(child.claim, Occupied):
            for row in child.rows:
                refusals.append(
                    Refusal(
                        row.group,
                        f"'{row.group}' cannot become '{child.display_name}': "
                        f"{child.claim.reason}",
                    )
                )
        for row in child.rows:
            if not row.variant:
                refusals.append(
                    Refusal(
                        row.group,
                        f"'{row.group}' names no variant after '{VARIANT_SEPARATOR}'. "
                        f"Name it <asset>{VARIANT_SEPARATOR}<variant>, or drop the "
                        "underscores.",
                    )
                )
            problem = scale_problem(row.piece)
            if problem is not None:
                refusals.append(Refusal(row.group, problem))
    return refusals
