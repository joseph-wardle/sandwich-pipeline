"""Stop every geometry variant's materials landing on the selected one.

Houdini also references each variant from the root prim, outside the variant,
so all variants' materials compose onto whichever mesh is selected.

Called from the Python LOP before `END` in the component config node.
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
