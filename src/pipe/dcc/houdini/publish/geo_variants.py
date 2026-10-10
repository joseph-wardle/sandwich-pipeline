"""Keep each geometry variant's material arcs inside that variant.

Component Geometry Variants references every variant's hidden
`/ASSET_geo_variant_N/ASSET` prim twice: from the matching `geo` variant, and
from the root prim itself. The variant gates the mesh, but the `mtl` variant
set inside each referenced prim is not gated, so every variant's GeomSubsets
and bindings compose onto whichever geometry is selected: subsets overlap,
indices run past the face count, and the renderer picks a texture at random.
Dropping the root-level copies leaves the variant set as the only path to each
variant's prim. Observed on Houdini 21.0.596.

Root references nothing carries stay: a single-variant builder reaches its
materials through one (`/ASSET_mtl_default`), and publishes made before
variants were named reach their geometry through them alone.

Delete this module when Houdini stops authoring the root-level references, or
when materials are authored inside each geometry variant instead of a shared
`mtl` variant set.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pxr import Sdf

from pipe.core.asset.paths import GEOMETRY_VARIANT_SET

log = logging.getLogger(__name__)


def confine_geo_variant_references(entry_layer: Path) -> None:
    """Rewrite every layer `entry_layer` payloads, saving only those that change."""
    layer = Sdf.Layer.FindOrOpen(str(entry_layer))
    root = layer.GetPrimAtPath(f"/{layer.defaultPrim}")
    for payload in root.payloadList.GetAddedOrExplicitItems():
        payload_layer = Sdf.Layer.FindOrOpen(
            layer.ComputeAbsolutePath(payload.assetPath)
        )
        payload_root = payload_layer.GetPrimAtPath(f"/{payload_layer.defaultPrim}")
        for reference in payload_root.referenceList.GetAddedOrExplicitItems():
            _drop_root_references_a_variant_carries(
                Sdf.Layer.FindOrOpen(
                    payload_layer.ComputeAbsolutePath(reference.assetPath)
                )
            )


def _drop_root_references_a_variant_carries(layer: Sdf.Layer) -> None:
    root = layer.GetPrimAtPath(f"/{layer.defaultPrim}")
    variant_set = root.variantSets.get(GEOMETRY_VARIANT_SET)
    if variant_set is None:
        return
    carried = [
        reference
        for variant in variant_set.variants.values()
        for reference in variant.primSpec.referenceList.GetAddedOrExplicitItems()
    ]
    references = root.referenceList.GetAddedOrExplicitItems()
    kept = [reference for reference in references if reference not in carried]
    if len(kept) == len(references):
        return
    root.referenceList.ClearEdits()
    root.referenceList.prependedItems = kept
    layer.Save()
    log.info(
        "%s: dropped %d root references the %s variant set already carries",
        layer.identifier,
        len(references) - len(kept),
        GEOMETRY_VARIANT_SET,
    )
