"""The pieces layer: everything Maya tells the builder about an assembly.

Metres, one reference per piece to the child's entry layer, one placement each
(ADR-0032). Maya writes it at publish; the headless publish reads it back to
find the children that must build before the assembly does.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Pcp, Sdf, Usd, UsdGeom

from pipe.core.asset.paths import (
    PIECES_LAYER_FILENAME,
    AssetPaths,
    production_relative_identifier,
)
from pipe.core.assembly.model import AssemblyError
from pipe.core.util.paths import get_production_path

# The component config renames this root to the asset's own name on publish.
PIECES_ROOT_PRIM = "ASSET"


def pieces_layer_for(stage: Usd.Stage) -> Sdf.Layer:
    """Build, in memory, the pieces layer for Maya's working `stage`."""
    layer = Sdf.Layer.CreateAnonymous(PIECES_LAYER_FILENAME)
    pieces = Usd.Stage.Open(layer)
    root = UsdGeom.Xform.Define(pieces, f"/{PIECES_ROOT_PRIM}")
    pieces.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(pieces, 1.0)
    UsdGeom.SetStageUpAxis(pieces, UsdGeom.Tokens.y)

    # The working stage is centimetres and a child publishes in metres, so a
    # placement keeps its rotation and scale and only its translation converts.
    to_metres = UsdGeom.GetStageMetersPerUnit(stage)
    for piece in stage.GetDefaultPrim().GetChildren():
        entry = AssetPaths(_child_root(piece)).entry_layer
        placement = UsdGeom.Xformable(piece).GetLocalTransformation()
        placement.SetTranslateOnly(placement.ExtractTranslation() * to_metres)

        prim = pieces.DefinePrim(root.GetPath().AppendChild(piece.GetName()))
        prim.GetReferences().AddReference(production_relative_identifier(entry))
        UsdGeom.Xformable(prim).MakeMatrixXform().Set(placement)
    return layer


def child_asset_roots(pieces_layer: Path) -> list[Path]:
    """The children an assembly's pieces layer references, each once, in order.

    Empty when `pieces_layer` does not exist, which is every component asset.
    """
    if not pieces_layer.is_file():
        return []
    layer = Sdf.Layer.FindOrOpen(str(pieces_layer))
    root = layer.GetPrimAtPath(f"/{layer.defaultPrim}")
    roots: list[Path] = []
    for piece in root.nameChildren:
        for reference in piece.referenceList.GetAddedOrExplicitItems():
            # Identifiers are production-root-relative, as `pieces_layer_for` writes them.
            child = AssetPaths.from_entry_layer(
                get_production_path() / reference.assetPath
            )
            if child.root not in roots:
                roots.append(child.root)
    return roots


def _child_root(piece: Usd.Prim) -> Path:
    """The child asset's directory, found through the piece's one payload."""
    payloads = [
        arc
        for arc in Usd.PrimCompositionQuery(piece).GetCompositionArcs()
        if arc.GetArcType() == Pcp.ArcTypePayload
    ]
    if len(payloads) != 1:
        raise AssemblyError(
            f"'{piece.GetName()}' is built from {len(payloads)} payloads and a piece "
            "has exactly one. Only pieces written by Split Assembly can be published; "
            "delete anything else from the assembly's stage."
        )
    source_layer = Path(
        payloads[0].GetTargetNode().layerStack.identifier.rootLayer.realPath
    )
    return AssetPaths.from_source_layer(source_layer).root
