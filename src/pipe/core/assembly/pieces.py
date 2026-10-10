"""What the layers on disk say about an assembly: its pieces, their origin, its kind."""

from __future__ import annotations

from pathlib import Path

from pxr import Pcp, Sdf, Usd, UsdGeom

from pipe.core.asset.paths import (
    GEOMETRY_VARIANT_SET,
    PIECES_LAYER_FILENAME,
    AssetPaths,
    production_relative_identifier,
)
from pipe.core.assembly.model import AssemblyError
from pipe.core.util.paths import get_production_path

# The component config renames this root to the asset's own name on publish.
PIECES_ROOT_PRIM = "ASSET"
_CONFIG_EMPTY_VARIANT = "__EMPTY"

ASSEMBLY_KEY = "assembly"
ASSEMBLY_KIND = "assembly"


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
    assembly = stage.GetDefaultPrim()
    for piece in assembly.GetFilteredChildren(Usd.PrimAllPrimsPredicate):
        source = Path(piece_edit_target(piece).GetLayer().realPath)
        entry = AssetPaths.from_source_layer(source).entry_layer
        placement = UsdGeom.Xformable(piece).GetLocalTransformation()
        placement.SetTranslateOnly(placement.ExtractTranslation() * to_metres)

        prim = pieces.DefinePrim(root.GetPath().AppendChild(piece.GetName()))
        prim.GetReferences().AddReference(production_relative_identifier(entry))
        # Every file in a child's `_src` is one geometry variant, named by the file.
        prim.GetVariantSets().SetSelection(GEOMETRY_VARIANT_SET, source.stem)
        UsdGeom.Xformable(prim).MakeMatrixXform().Set(placement)
    return layer


def piece_edit_target(piece: Usd.Prim) -> Usd.EditTarget:
    """An edit target across the piece's one payload, into the child's own layer."""
    payloads = [
        arc
        for arc in Usd.PrimCompositionQuery(piece).GetCompositionArcs()
        if arc.GetArcType() == Pcp.ArcTypePayload
    ]
    if len(payloads) != 1:
        raise AssemblyError(_payload_problem(piece, len(payloads)))
    # The node belongs to the arc, and dangles once the arc is released: build
    # the target while `payloads` still holds it.
    node = payloads[0].GetTargetNode()
    return Usd.EditTarget(node.layerStack.identifier.rootLayer, node)


def _payload_problem(piece: Usd.Prim, composed: int) -> str:
    """Why `piece` composes through `composed` payloads instead of one, for the artist."""
    name = piece.GetName()
    if not piece.HasAuthoredPayloads():
        return (
            f"'{name}' has no payload, so it was not made by Split Pieces. Only a "
            "piece belongs in the assembly's stage; delete anything else from it."
        )
    if not piece.IsLoaded():
        return (
            f"'{name}' is unloaded, so its child cannot be reached. Right-click it "
            "in the Outliner and choose Load, then try again."
        )
    if composed == 0:
        authored = piece.GetMetadata("payload").GetAddedOrExplicitItems()
        files = ", ".join(payload.assetPath for payload in authored)
        return (
            f"'{name}' points at {files}, which could not be opened. Check that the "
            "child's file is still under the production root, then try again."
        )
    return (
        f"'{name}' is built from {composed} payloads and a piece has exactly one. "
        "Only a piece made by Split Pieces belongs in the assembly's stage; delete "
        "anything else from it."
    )


def child_variants(pieces_layer: Path) -> dict[Path, set[str]]:
    """Each child the pieces layer references, in order, with the variants it places.

    Empty when `pieces_layer` does not exist, which is every component asset.
    """
    if not pieces_layer.is_file():
        return {}
    layer = Sdf.Layer.FindOrOpen(str(pieces_layer))
    root = layer.GetPrimAtPath(f"/{layer.defaultPrim}")
    children: dict[Path, set[str]] = {}
    for piece in root.nameChildren:
        variant = piece.variantSelections[GEOMETRY_VARIANT_SET]
        for reference in piece.referenceList.GetAddedOrExplicitItems():
            # Identifiers are production-root-relative, as `pieces_layer_for` writes them.
            child = AssetPaths.from_entry_layer(
                get_production_path() / reference.assetPath
            )
            children.setdefault(child.root, set()).add(variant)
    return children


def placed_variants(child_root: Path) -> set[str]:
    """The variants the assemblies a child was split from place of it."""
    sources = AssetPaths(child_root).publish_source_dir
    assemblies = {
        assembly
        for source in sorted(sources.glob("*.usd"))
        if (assembly := assembly_of(source)) is not None
    }
    placed: set[str] = set()
    for assembly in assemblies:
        pieces = AssetPaths(get_production_path() / assembly).pieces_layer
        for root, variants in child_variants(pieces).items():
            # By name: the hip and the pieces layer may reach /job by different mounts.
            if root.name == child_root.name:
                placed |= variants
    return placed


def published_variants(child_root: Path) -> set[str]:
    """The geometry variants a child's current publish provides.

    A single-branch publish names no variant, so its one source names it; with
    a second source on disk the publish is stale and provides nothing.
    """
    paths = AssetPaths(child_root)
    if not paths.entry_layer.is_file():
        return set()
    stage = Usd.Stage.Open(str(paths.entry_layer))
    variant_set = (
        stage.GetDefaultPrim().GetVariantSets().GetVariantSet(GEOMETRY_VARIANT_SET)
    )
    names = set(variant_set.GetVariantNames()) - {_CONFIG_EMPTY_VARIANT}
    if names:
        return names
    sources = {source.stem for source in paths.publish_source_dir.glob("*.usd")}
    return sources if len(sources) == 1 else set()


def stamp_assembly(layer: Sdf.Layer, assembly_root: Path) -> None:
    """Record on `layer`, without saving it, the assembly the child was split from."""
    layer.customLayerData = {
        **layer.customLayerData,
        ASSEMBLY_KEY: production_relative_identifier(assembly_root),
    }


def assembly_of(source_layer: Path) -> str | None:
    """The assembly `source_layer` was split from, or None for a layer made by hand."""
    layer = Sdf.Layer.FindOrOpen(str(source_layer))
    if layer is None:
        return None
    return layer.customLayerData.get(ASSEMBLY_KEY)


def mark_published_assembly(entry_layer: Path) -> None:
    """Rewrite the root kind on `entry_layer` and on its payload, and save both."""
    layer = Sdf.Layer.FindOrOpen(str(entry_layer))
    root = layer.GetPrimAtPath(f"/{layer.defaultPrim}")
    for payload in root.payloadList.GetAddedOrExplicitItems():
        _set_root_kind(
            Sdf.Layer.FindOrOpen(layer.ComputeAbsolutePath(payload.assetPath))
        )
    _set_root_kind(layer)


def _set_root_kind(layer: Sdf.Layer) -> None:
    layer.GetPrimAtPath(f"/{layer.defaultPrim}").kind = ASSEMBLY_KIND
    layer.Save()
