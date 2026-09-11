"""Split one piece out of an assembly: export it, payload it back, delete it."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from maya import cmds as mc
from pxr import Gf, Usd, UsdGeom

from pipe.core.asset.paths import production_relative_identifier
from pipe.core.assembly.model import Piece, PieceTarget, SplitError, SplitResult
from pipe.core.assembly.normalize import (
    SOURCE_LAYER_LINEAR_UNIT,
    SOURCE_LAYER_UP_AXIS,
    ScaleCheck,
    bounds_match,
    inspect_scale,
    normalization_matrix,
    placement_for,
    prim_point_bounds,
)
from pipe.dcc.maya.assembly.scan import (
    renderable_meshes,
    world_matrix,
    world_point_bounds,
)
from pipe.dcc.maya.assembly.stage import ensure_assembly_stage, stage_shape
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.usd_export import export_selection

log = logging.getLogger(__name__)

_ROOT_PRIM_TYPE = "xform"

_SHADING_MODE = "useRegistry"

_PLACEMENT_TOLERANCE = 1e-3

_PENDING_SUFFIX = ".writing.usd"


def split_piece(
    piece: Piece, target: PieceTarget, *, assembly_name: str
) -> SplitResult:
    """Move `piece` out of the Maya scene and into `target`, in place."""
    _refuse_existing_model(target)
    _refuse_foreign_scene_units()
    stage_shape()  # Refuse an ambiguous scene before anything is written.
    scale = _bakeable_scale(piece)
    bounds_before = _piece_bounds(piece)

    normalization = _install_source_layer(piece, target, scale)
    placement = placement_for(piece.world_matrix, normalization)

    stage = ensure_assembly_stage()
    prim = _author_piece_prim(stage, assembly_name, target, placement)
    bounds_after = _prim_world_bounds(prim)
    _confirm_unmoved(piece, target, stage, prim, bounds_before, bounds_after)

    mc.delete(piece.node)

    return SplitResult(
        piece_name=piece.name,
        asset_name=target.asset_name,
        source_layer=target.source_layer,
        prim_path=str(prim.GetPath()),
        placement=placement,
        world_bounds_before=bounds_before,
        world_bounds_after=bounds_after,
    )


def _refuse_existing_model(target: PieceTarget) -> None:
    """Refuse an asset that already holds a model, whatever it is called."""
    existing = [target.asset_root / name for name in ("model.mb", "model.blend")]
    source_dir = target.source_layer.parent
    if source_dir.is_dir():
        existing += [
            layer
            for layer in sorted(source_dir.glob("*.usd"))
            if not layer.name.endswith(_PENDING_SUFFIX)
        ]

    found = next((path for path in existing if path.exists()), None)
    if found is not None:
        raise SplitError(
            f"'{target.asset_name}' already has a model at {found}. Split under a "
            "different name, or edit the existing asset from the assembly it was "
            "split into."
        )


def _refuse_foreign_scene_units() -> None:
    """Refuse a scene whose units a split would silently reinterpret."""
    unit = mc.currentUnit(query=True, linear=True)
    if unit != SOURCE_LAYER_LINEAR_UNIT:
        raise SplitError(
            f"This scene's working units are {unit}, and a split writes "
            "centimetres. Scale the assembly's geometry to centimetres first — "
            "changing the unit preference on its own relabels the scene and "
            "leaves everything 100x off."
        )

    up_axis = str(mc.upAxis(query=True, axis=True)).upper()
    if up_axis != SOURCE_LAYER_UP_AXIS:
        raise SplitError(
            f"This scene is {up_axis}-up, and a split writes "
            f"{SOURCE_LAYER_UP_AXIS}-up. Rotate the assembly upright and freeze "
            "its transformations before splitting."
        )


def _bakeable_scale(piece: Piece) -> float:
    check = inspect_scale(piece.world_matrix)
    if not check.bakeable:
        factors = ", ".join(f"{f:g}" for f in check.factors)
        raise SplitError(
            f"'{piece.name}' has {_scale_fault(check)} (scale {factors}) and "
            "cannot be baked into a child asset. Freeze the piece's "
            "transformations, or correct its scale, then split again."
        )
    return check.factor


def _scale_fault(check: ScaleCheck) -> str:
    if not check.positive:
        return "negative or mirrored scale"
    if not check.orthogonal:
        return "sheared axes"
    return "non-uniform scale"


def _piece_bounds(piece: Piece) -> Gf.Range3d:
    """Measure the piece where it stands, refusing one there is nothing to measure."""
    if not renderable_meshes(piece.node):
        raise SplitError(
            f"'{piece.name}' holds no visible geometry, so a split would produce "
            "an empty asset. Split a group that contains the piece's meshes."
        )
    return world_point_bounds(piece.node)


def _install_source_layer(
    piece: Piece, target: PieceTarget, scale: float
) -> Gf.Matrix4d:
    """Write `target.source_layer`, normalized, and return the normalization."""
    pending = target.source_layer.with_name(target.source_layer.stem + _PENDING_SUFFIX)
    try:
        with maintain_selection():
            mc.select(piece.node, replace=True)
            export_selection(
                pending,
                stripNamespaces=True,
                rootPrim=target.asset_name,
                rootPrimType=_ROOT_PRIM_TYPE,
                defaultPrim=target.asset_name,
                unit=SOURCE_LAYER_LINEAR_UNIT,
                shadingMode=_SHADING_MODE,
            )
        normalization = _normalize_layer(pending, target, scale)
        os.replace(pending, target.source_layer)
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return normalization


def _normalize_layer(layer: Path, target: PieceTarget, scale: float) -> Gf.Matrix4d:
    """Rewrite the exported layer so its contents rest at the origin."""
    stage = Usd.Stage.Open(str(layer))
    root = stage.GetDefaultPrim()
    group = _exported_group(root, target)

    if _clear_transform(root):
        log.warning("Cleared an unexpected transform on the exported root of %s", layer)
    _clear_transform(group)

    normalization = normalization_matrix(prim_point_bounds(root), scale)
    UsdGeom.Xformable(group).MakeMatrixXform().Set(normalization)
    stage.GetRootLayer().Save()
    return normalization


def _exported_group(root: Usd.Prim, target: PieceTarget) -> Usd.Prim:
    """Return the one prim under the export root that carries the piece's geometry."""
    candidates = [child for child in root.GetChildren() if child.IsA(UsdGeom.Xformable)]
    if len(candidates) != 1:
        names = ", ".join(child.GetName() for child in root.GetChildren()) or "nothing"
        raise SplitError(
            f"'{target.asset_name}' did not export as a single group — {root.GetPath()} "
            f"contains {names}. Split a group that holds all of the piece's geometry."
        )
    return candidates[0]


def _clear_transform(prim: Usd.Prim) -> bool:
    """Drop any transform on `prim`, reporting whether there was one."""
    xformable = UsdGeom.Xformable(prim)
    had_transform = bool(xformable.GetXformOpOrderAttr().HasAuthoredValue())
    xformable.ClearXformOpOrder()
    return had_transform


def _author_piece_prim(
    stage: Usd.Stage,
    assembly_name: str,
    target: PieceTarget,
    placement: Gf.Matrix4d,
) -> Usd.Prim:
    """Payload the child back into the assembly, in the place the piece stood."""
    root = UsdGeom.Xform.Define(stage, f"/{assembly_name}")
    stage.SetDefaultPrim(root.GetPrim())

    prim = stage.DefinePrim(f"/{assembly_name}/{target.asset_name}")
    prim.GetPayloads().AddPayload(production_relative_identifier(target.source_layer))
    UsdGeom.Xformable(prim).MakeMatrixXform().Set(placement)
    return prim


def _confirm_unmoved(
    piece: Piece,
    target: PieceTarget,
    stage: Usd.Stage,
    prim: Usd.Prim,
    before: Gf.Range3d,
    after: Gf.Range3d,
) -> None:
    """Prove the payloaded piece stands where the Maya piece did, or undo the split."""
    if bounds_match(before, after, _PLACEMENT_TOLERANCE):
        return

    stage.RemovePrim(prim.GetPath())
    target.source_layer.unlink(missing_ok=True)
    raise SplitError(
        f"'{piece.name}' did not come back in the same place, so nothing was "
        f"changed. It occupied {before}, and composes at {after}. Check that "
        f"{target.source_layer} is under the production root and that the "
        "assembly's stage has not been moved or scaled."
    )


def _prim_world_bounds(prim: Usd.Prim) -> Gf.Range3d:
    """Return `prim`'s point bounds in Maya's world space, not the stage's."""
    return prim_point_bounds(prim, _stage_world_matrix())


def _stage_world_matrix() -> Gf.Matrix4d:
    shape = stage_shape()
    if shape is None:
        return Gf.Matrix4d(1.0)
    transform = mc.listRelatives(shape, parent=True, fullPath=True)[0]
    return world_matrix(transform)
