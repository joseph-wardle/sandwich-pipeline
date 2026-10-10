"""Split one piece out of an assembly: export it, payload it back, delete it."""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from pathlib import Path

from maya import cmds as mc
from pxr import Gf, Usd, UsdGeom

from pipe.core.asset.paths import (
    DEFAULT_GEOMETRY_VARIANT,
    AssetPaths,
    production_relative_identifier,
)
from pipe.core.assembly.model import AssemblyError, Piece, PieceTarget
from pipe.core.assembly.normalize import (
    SOURCE_LAYER_LINEAR_UNIT,
    bounds_match,
    clear_transform,
    inspect_scale,
    normalization_matrix,
    placement_for,
    prim_point_bounds,
)
from pipe.core.assembly.pieces import stamp_assembly
from pipe.core.assembly.plan import existing_model_problem, scale_problem
from pipe.dcc.maya.assembly.scan import (
    renderable_meshes,
    scene_units_problem,
    world_matrix,
    world_point_bounds,
)
from pipe.dcc.maya.assembly.stage import (
    STAGE_TRANSFORM_NAME,
    ensure_assembly_stage,
    placed_prim_names,
    stage_shape,
)
from pipe.dcc.maya.util.materials import delete_unused_shading_groups, shading_groups
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.usd_export import export_selection

log = logging.getLogger(__name__)

_ROOT_PRIM_TYPE = "xform"

_SHADING_MODE = "useRegistry"

_PLACEMENT_TOLERANCE = 1e-3
_PENDING_DIRNAME = ".writing"


def split_piece(piece: Piece, target: PieceTarget, *, assembly_root: Path) -> None:
    """Move `piece` out of the Maya scene and into `target`, in place."""
    _refuse_existing_model(target, assembly_root)
    _refuse_scene_units()
    stage_shape()  # Refuse an ambiguous scene before anything is written.
    scale = _bakeable_scale(piece)
    bounds_before = _piece_bounds(piece)

    _link_textures(target, assembly_root)
    normalization = _install_source_layer(piece, target, scale, assembly_root)
    placement = placement_for(piece.world_matrix, normalization)

    stage = ensure_assembly_stage()
    prim = _author_piece_prim(stage, assembly_root.name, target, placement)
    bounds_after = _prim_world_bounds(prim)
    _confirm_unmoved(piece, target, stage, prim, bounds_before, bounds_after)

    materials = shading_groups([piece.node])
    mc.delete(piece.node)
    # The piece's materials went with it; one still used by another group stays.
    delete_unused_shading_groups(materials)


def _refuse_existing_model(target: PieceTarget, assembly_root: Path) -> None:
    """The plan's check, again: the scene or disk may have changed since it ran."""
    problem = existing_model_problem(
        target.asset_root,
        target.asset_name,
        [target.variant],
        assembly_root=assembly_root,
        placed=placed_prim_names(),
    )
    if problem is not None:
        raise AssemblyError(problem)


def _refuse_scene_units() -> None:
    problem = scene_units_problem()
    if problem is not None:
        raise AssemblyError(problem)


def _bakeable_scale(piece: Piece) -> float:
    problem = scale_problem(piece)
    if problem is not None:
        raise AssemblyError(problem)
    return inspect_scale(piece.world_matrix).factor


def _piece_bounds(piece: Piece) -> Gf.Range3d:
    """Measure the piece where it stands, refusing one there is nothing to measure."""
    if not renderable_meshes(piece.node):
        raise AssemblyError(
            f"'{piece.name}' holds no visible geometry, so a split would produce "
            "an empty asset. Split a group that contains the piece's meshes."
        )
    return world_point_bounds(piece.node)


def _link_textures(target: PieceTarget, assembly_root: Path) -> None:
    """Make the child's textures for this variant the assembly's."""
    link = AssetPaths(target.asset_root).publish_textures_variant_dir(target.variant)
    textures = AssetPaths(assembly_root).publish_textures_variant_dir(
        DEFAULT_GEOMETRY_VARIANT
    )
    # Relative, so the pair survives a move together and either mount of /job.
    relative = os.path.relpath(textures, link.parent)
    if link.is_symlink() and os.readlink(link) == relative:
        return
    if link.exists() or link.is_symlink():
        raise AssemblyError(
            f"'{target.asset_name}' already has its own textures at {link}, but a "
            f"split piece shares its assembly's ({textures}). Split under a "
            "different name, or delete that folder if it is a leftover."
        )
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(relative, link)


def _install_source_layer(
    piece: Piece, target: PieceTarget, scale: float, assembly_root: Path
) -> Gf.Matrix4d:
    """Write `target.source_layer`, normalized, and return the normalization."""
    pending = target.source_layer.parent / _PENDING_DIRNAME / target.source_layer.name
    try:
        with maintain_selection():
            mc.select(piece.node, replace=True)
            export_selection(
                pending,
                stripNamespaces=True,
                rootPrim=target.prim_name,
                rootPrimType=_ROOT_PRIM_TYPE,
                defaultPrim=target.prim_name,
                unit=SOURCE_LAYER_LINEAR_UNIT,
                shadingMode=_SHADING_MODE,
            )
        normalization = _prepare_layer(pending, target, scale, assembly_root)
        os.replace(pending, target.source_layer)
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    finally:
        with suppress(OSError):
            pending.parent.rmdir()
    return normalization


def _prepare_layer(
    layer: Path, target: PieceTarget, scale: float, assembly_root: Path
) -> Gf.Matrix4d:
    """Rest the exported contents at the origin and record where they came from."""
    stage = Usd.Stage.Open(str(layer))
    root_layer = stage.GetRootLayer()
    root = stage.GetDefaultPrim()
    group = _exported_group(root, target)

    if clear_transform(root_layer.GetPrimAtPath(root.GetPath())):
        log.warning("Cleared an unexpected transform on the exported root of %s", layer)
    clear_transform(root_layer.GetPrimAtPath(group.GetPath()))

    normalization = normalization_matrix(prim_point_bounds(root), scale)
    UsdGeom.Xformable(group).MakeMatrixXform().Set(normalization)
    stamp_assembly(root_layer, assembly_root)
    root_layer.Save()
    return normalization


def _exported_group(root: Usd.Prim, target: PieceTarget) -> Usd.Prim:
    """Return the one prim under the export root that carries the piece's geometry."""
    candidates = [child for child in root.GetChildren() if child.IsA(UsdGeom.Xformable)]
    if len(candidates) != 1:
        names = ", ".join(child.GetName() for child in root.GetChildren()) or "nothing"
        raise AssemblyError(
            f"'{target.asset_name}' did not export as a single group — {root.GetPath()} "
            f"contains {names}. Split a group that holds all of the piece's geometry."
        )
    return candidates[0]


def _author_piece_prim(
    stage: Usd.Stage,
    assembly_name: str,
    target: PieceTarget,
    placement: Gf.Matrix4d,
) -> Usd.Prim:
    """Payload the child back into the assembly, in the place the piece stood."""
    root = UsdGeom.Xform.Define(stage, f"/{assembly_name}")
    stage.SetDefaultPrim(root.GetPrim())

    prim = stage.DefinePrim(root.GetPath().AppendChild(target.prim_name))
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
    raise AssemblyError(
        f"'{piece.name}' did not come back in the same place, so it was not split. "
        f"It spanned {_range_text(before)} cm and came back at "
        f"{_range_text(after)}. Check that the assembly's stage "
        f"({STAGE_TRANSFORM_NAME}) has not been moved or scaled, then split again; "
        "if it has not, ask a TD."
    )


def _range_text(bounds: Gf.Range3d) -> str:
    """'(0, 0, 0) to (10, 20, 5)', as an artist reads a bounding box."""
    corners = (bounds.GetMin(), bounds.GetMax())
    return " to ".join(
        "(" + ", ".join(f"{value:.4g}" for value in corner) + ")" for corner in corners
    )


def _prim_world_bounds(prim: Usd.Prim) -> Gf.Range3d:
    """Return `prim`'s point bounds in Maya's world space, not the stage's."""
    return prim_point_bounds(prim, _stage_world_matrix())


def _stage_world_matrix() -> Gf.Matrix4d:
    shape = stage_shape()
    assert shape is not None, "runs after ensure_assembly_stage"
    transform = mc.listRelatives(shape, parent=True, fullPath=True)[0]
    return world_matrix(transform)
