"""Variant discovery and planning for SKD Houdini component graphs.

This module intentionally has no Houdini-node side effects. It discovers
geometry/material publish variants and returns a deterministic build plan that
`pipe.h.nodelayouts` can apply to the LOP network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

USD_EXTENSIONS = frozenset({".usd", ".usda", ".usdc"})
USD_EXTENSION_ORDER = (".usd", ".usdc", ".usda")
DEFAULT_GEO_VARIANT = "main"
DEFAULT_MAT_VARIANT = "main"
GEO_SOURCE_DIR = Path("publish") / "_src"
TEX_SOURCE_DIR = Path("publish") / "tex"
_NODE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True)
class GeometryVariantPlan:
    """One geometry variant branch and its material variants."""

    name: str
    source_path: Path
    source_exists: bool
    material_variants: tuple[str, ...]
    existing_material_variants: frozenset[str]


@dataclass(frozen=True)
class VariantBuildPlan:
    """Deterministic variant build plan derived from publish folders."""

    hip_root: Path
    geo_source_dir: Path
    tex_source_dir: Path
    geometry_variants: tuple[GeometryVariantPlan, ...]
    warnings: tuple[str, ...]


def geo_source_expression(variant_name: str) -> str:
    """Return a `$HIP` expression for a geometry variant source USD."""
    variant = variant_name.strip() or DEFAULT_GEO_VARIANT
    return f"$HIP/{GEO_SOURCE_DIR.as_posix()}/{variant}.usd"


def default_geo_source_expression() -> str:
    """Return the canonical default source USD expression."""
    return geo_source_expression(DEFAULT_GEO_VARIANT)


def to_hip_expression(path: Path, *, hip_root: Path) -> str:
    """Convert a path to `$HIP/...` form when possible."""
    candidate = path.expanduser()
    root = hip_root.expanduser()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return candidate.as_posix()
    return f"$HIP/{relative.as_posix()}"


def node_token(value: str, *, fallback: str = "main") -> str:
    """Convert arbitrary variant names into deterministic node-name tokens."""
    token = _NODE_TOKEN_RE.sub("_", value.strip())
    token = re.sub(r"_+", "_", token).strip("_")
    if not token:
        token = fallback
    if token[0].isdigit():
        token = f"v_{token}"
    return token.lower()


def discover_build_plan(
    hip_root: Path,
    *,
    preferred_geo_variants: tuple[str, ...] | None = None,
    preferred_mat_variants: tuple[str, ...] | None = None,
) -> VariantBuildPlan:
    """Discover geometry/material variants and return a deterministic build plan.

    Args:
        hip_root: Asset HIP root.
        preferred_geo_variants: Optional declared geometry variants to include
            even when publishes are missing.
        preferred_mat_variants: Optional declared material variants to include
            even when publishes are missing.
    """
    return _discover_build_plan(
        hip_root=hip_root,
        preferred_geo_variants=preferred_geo_variants,
        preferred_mat_variants=preferred_mat_variants,
    )


def _discover_build_plan(
    *,
    hip_root: Path,
    preferred_geo_variants: tuple[str, ...] | None,
    preferred_mat_variants: tuple[str, ...] | None,
) -> VariantBuildPlan:
    root = hip_root.expanduser()
    geo_root = root / GEO_SOURCE_DIR
    tex_root = root / TEX_SOURCE_DIR

    declared_geo = _normalize_variants(preferred_geo_variants)
    declared_mat = _normalize_variants(preferred_mat_variants)

    warnings: list[str] = []
    geo_sources = dict(_discover_geometry_sources(geo_root))
    published_geo = tuple(sorted(geo_sources.keys(), key=str.casefold))

    geo_names = _ordered_union(declared_geo, published_geo)
    if not geo_names:
        warnings.append(
            f"No geometry variants discovered in declarations or {geo_root}; using fallback '{DEFAULT_GEO_VARIANT}'."
        )
        geo_names = (DEFAULT_GEO_VARIANT,)

    mats_by_geo: dict[str, tuple[str, ...]] = {}
    all_published_mats: set[str] = set()
    for geo_name in geo_names:
        mat_variants = _discover_material_variants(tex_root / geo_name)
        mats_by_geo[geo_name] = mat_variants
        all_published_mats.update(mat_variants)

    default_mats = _ordered_union(
        declared_mat, tuple(sorted(all_published_mats, key=str.casefold))
    )
    if not default_mats:
        default_mats = (DEFAULT_MAT_VARIANT,)

    geometry_plans: list[GeometryVariantPlan] = []
    for geo_name in geo_names:
        source_path = geo_sources.get(geo_name, geo_root / f"{geo_name}.usd")
        source_exists = source_path.exists()

        if declared_geo and geo_name not in geo_sources:
            warnings.append(
                f"Geometry publish missing for declared variant '{geo_name}' at {source_path}."
            )

        published_mats = mats_by_geo.get(geo_name, ())
        mat_variants = _ordered_union(
            declared_mat if declared_mat else published_mats,
            published_mats if declared_mat else default_mats,
        )
        if not mat_variants:
            warnings.append(
                f"No texture variants found for geometry '{geo_name}'; using material fallback '{DEFAULT_MAT_VARIANT}'."
            )
            mat_variants = (DEFAULT_MAT_VARIANT,)

        if declared_mat:
            missing_materials = [
                material for material in declared_mat if material not in published_mats
            ]
            if missing_materials:
                formatted = ", ".join(missing_materials)
                warnings.append(
                    f"Texture publishes missing for geometry '{geo_name}' materials: {formatted}."
                )
        elif not published_mats:
            warnings.append(
                f"No texture publishes found for geometry '{geo_name}' under {tex_root / geo_name}."
            )

        geometry_plans.append(
            GeometryVariantPlan(
                name=geo_name,
                source_path=source_path,
                source_exists=source_exists,
                material_variants=mat_variants,
                existing_material_variants=frozenset(published_mats),
            )
        )

    return VariantBuildPlan(
        hip_root=root,
        geo_source_dir=geo_root,
        tex_source_dir=tex_root,
        geometry_variants=tuple(geometry_plans),
        warnings=tuple(warnings),
    )


def _discover_geometry_sources(root: Path) -> tuple[tuple[str, Path], ...]:
    if not root.is_dir():
        return ()

    by_variant: dict[str, Path] = {}
    for item in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if not item.is_file():
            continue
        if item.name.startswith("."):
            continue
        if item.suffix.lower() not in USD_EXTENSIONS:
            continue

        variant_name = item.stem.strip()
        if not variant_name:
            continue

        current = by_variant.get(variant_name)
        if current is None or _usd_sort_key(item) < _usd_sort_key(current):
            by_variant[variant_name] = item

    return tuple(sorted(by_variant.items(), key=lambda entry: entry[0].casefold()))


def _discover_material_variants(root: Path) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    names = [
        directory.name.strip()
        for directory in sorted(root.iterdir(), key=lambda path: path.name.casefold())
        if directory.is_dir() and not directory.name.startswith(".")
    ]
    filtered = [name for name in names if name]
    return tuple(sorted(set(filtered), key=str.casefold))


def _normalize_variants(values: tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = (raw or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return tuple(normalized)


def _ordered_union(
    primary: tuple[str, ...], secondary: tuple[str, ...]
) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for sequence in (primary, secondary):
        for value in sequence:
            name = (value or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(name)
    return tuple(merged)


def _usd_sort_key(path: Path) -> tuple[int, str]:
    suffix = path.suffix.lower()
    try:
        index = USD_EXTENSION_ORDER.index(suffix)
    except ValueError:
        index = len(USD_EXTENSION_ORDER)
    return index, path.name.casefold()


__all__ = [
    "DEFAULT_GEO_VARIANT",
    "DEFAULT_MAT_VARIANT",
    "GEO_SOURCE_DIR",
    "GeometryVariantPlan",
    "TEX_SOURCE_DIR",
    "VariantBuildPlan",
    "default_geo_source_expression",
    "discover_build_plan",
    "geo_source_expression",
    "node_token",
    "to_hip_expression",
]
