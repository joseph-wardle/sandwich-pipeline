"""Keep each geometry variant's material arcs inside that variant.

Component Geometry Variants (Houdini 21.0.596) builds every variant from a
hidden `/ASSET_geo_variant_N/ASSET` prim and references that prim twice: from
the matching `geo` variant and directly from the root prim. The direct copy is
stock design and harmless for geometry, which the hidden prim keeps inside a
variant the root selection gates. Our builders give every branch its own
Component Material, and Add Variant leaves those arcs (the `/ASSET_mtl_default`
reference and the `mtl` variant set) outside the variant. Through the ungated
root copies every variant's GeomSubsets and bindings compose onto whichever
mesh is selected: subsets overlap, indices run past the face count, the
renderer picks a texture at random and XPU dies at startup.

Dropping a root reference the `geo` variant set already carries changes nothing
for the selected variant (its variant arc is stronger) and withdraws the other
variants' opinions. Root references no variant carries stay: a single-variant
builder reaches its materials through one, and publishes made before variants
were named reach their geometry through them alone.

One call site: the Python LOP before `END` inside the config node
(`lnd_componentconfig`). Component Output flattens the geo layer from that
`END`, which the config node tags as the geometry source, and ignores anything
wired after it, so the strip must sit there to reach the published file as well
as the builder viewport, Explore Variants and the lookdev turnaround. Delete this
module when each branch's material arcs are authored inside its geometry variant.
"""

from __future__ import annotations

from pxr import Sdf, Usd

from pipe.core.asset.paths import GEOMETRY_VARIANT_SET


def confine_geo_variant_references(stage: Usd.Stage) -> int:
    """Remove root references the `geo` variant set also carries; returns how many."""
    dropped = 0
    for prim in stage.GetPseudoRoot().GetChildren():
        specs = [
            spec
            for layer in stage.GetLayerStack()
            if (spec := layer.GetPrimAtPath(prim.GetPath()))
        ]
        carried = {
            reference for spec in specs for reference in _variant_references(spec)
        }
        for reference in _root_references(specs):
            if reference in carried:
                prim.GetReferences().RemoveReference(reference)
                dropped += 1
    return dropped


def _root_references(specs: list[Sdf.PrimSpec]) -> list[Sdf.Reference]:
    """The references the layer stack composes onto the prim itself."""
    references: list[Sdf.Reference] = []
    for spec in reversed(specs):
        if spec.HasInfo("references"):
            references = spec.GetInfo("references").ApplyOperations(references) or []
    return references


def _variant_references(spec: Sdf.PrimSpec) -> list[Sdf.Reference]:
    variant_set = spec.variantSets.get(GEOMETRY_VARIANT_SET)
    if variant_set is None:
        return []
    return [
        reference
        for variant in variant_set.variants.values()
        for reference in variant.primSpec.referenceList.GetAddedOrExplicitItems()
    ]
