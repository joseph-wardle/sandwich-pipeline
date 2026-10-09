"""Edit a piece in place: pull it into Maya, merge it back into the child's own layer.

A piece's geometry lives in the child asset's source layer, never in the assembly's
own layer, so a merge has to be aimed across the payload arc that brought it in.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import cast

import maya.api.OpenMaya as om
import mayaUsd.ufe
from maya import cmds as mc
from mayaUsd.lib import PrimUpdaterManager
from pxr import Gf, Pcp, Sdf, Tf, Usd, UsdGeom

from pipe.core.asset.paths import production_relative_identifier
from pipe.core.assembly.model import AssemblyError, EditError
from pipe.core.assembly.normalize import clear_transform
from pipe.dcc.maya.assembly.stage import stage_shape
from pipe.dcc.maya.util.materials import (
    delete_unused_shading_groups,
    material_problems,
)

log = logging.getLogger(__name__)

_HUD_NAME = "assemblyEditTarget"
_HUD_LABEL = "Piece edits save to"
_HUD_SECTION = 0

_MATERIAL_TYPE = "Material"
_PLACEMENT_TOLERANCE = 1e-6


def edit_piece(prim: Usd.Prim) -> str:
    """Pull `prim` into the Maya scene to be modelled, and return the pulled group.

    Adds nodes to the scene and puts the edit-target HUD in the viewport.
    """
    stage = prim.GetStage()
    path = prim.GetPath()

    already_open = open_piece(stage)
    if already_open is not None:
        raise EditError(
            f"'{already_open.GetName()}' is already open for editing. Save or "
            "discard it before opening another piece."
        )

    _refuse_non_piece(stage, prim)
    # Refuse a piece with no payload before anything is pulled.
    child_layer = child_source_layer(prim)
    _release_materials(prim, child_layer)
    ufe_path = _ufe_path(prim)

    # mayaUsdPlugin's commands are not in the maya stubs.
    mc.mayaUsdEditAsMaya(ufe_path)  # type: ignore
    maya_node = pulled_maya_node(stage.GetPrimAtPath(path))
    install_edit_hud()
    return maya_node


def merge_piece(stage: Usd.Stage) -> None:
    """Write the open piece's Maya edits into its child asset's layer, and save it."""
    piece = open_piece(stage)
    if piece is None:
        raise EditError("No piece is open for editing, so there is nothing to save.")

    child_target = _child_edit_target(piece)
    child_layer = child_target.GetLayer()
    maya_node = pulled_maya_node(piece)
    _refuse_moved(piece, maya_node)
    _refuse_material_problems(piece, maya_node)

    assembly_before = stage.GetRootLayer().ExportToString()
    with Usd.EditContext(stage, child_target):
        mc.mayaUsdMergeToUsd(maya_node)  # type: ignore
    # The merge writes the pulled group's transform, the placement, onto the
    # child's root; the child must stay at the origin or it is placed twice.
    clear_transform(
        child_layer.GetPrimAtPath(child_target.MapToSpecPath(piece.GetPath()))
    )
    _confirm_assembly_untouched(stage, piece, child_layer, assembly_before)

    delete_unused_shading_groups(_material_names(child_layer))
    remove_edit_hud()
    _save_child_layer(piece, child_layer)


def open_piece(stage: Usd.Stage) -> Usd.Prim | None:
    """The piece open for editing, refusing a scene that has more than one."""
    root = stage.GetDefaultPrim()
    if not root:
        return None

    pulled = [
        prim
        for prim in root.GetAllChildren()
        if PrimUpdaterManager.readPullInformation(prim)
    ]
    if len(pulled) > 1:
        names = ", ".join(prim.GetName() for prim in pulled)
        raise EditError(
            f"{len(pulled)} pieces are open for editing at once ({names}), and an "
            "assembly edits one at a time. Merge or discard all but one, then try "
            "again."
        )
    return pulled[0] if pulled else None


def pulled_maya_node(prim: Usd.Prim) -> str:
    """The Maya group a pulled piece is being edited as."""
    node = PrimUpdaterManager.readPullInformation(prim)
    if not node or not mc.objExists(node):
        raise EditError(
            f"'{prim.GetName()}' is marked as open for editing but Maya has no "
            "editable copy of it. Save the scene, reopen it, and try again."
        )
    return node


def child_source_layer(prim: Usd.Prim) -> Sdf.Layer:
    """The layer a piece's geometry comes from, which is where its edits belong."""
    return _child_edit_target(prim).GetLayer()


def install_edit_hud() -> None:
    """Put a line in the viewport naming the layer the open piece will save to."""
    remove_edit_hud()
    mc.headsUpDisplay(
        _HUD_NAME,
        section=_HUD_SECTION,
        block=cast(int, mc.headsUpDisplay(nextFreeBlock=_HUD_SECTION)),
        blockSize="small",
        label=_HUD_LABEL,
        labelFontSize="small",
        dataFontSize="small",
        dataWidth=64,
        command=hud_text,
        attachToRefresh=True,
    )


def remove_edit_hud() -> None:
    if mc.headsUpDisplay(_HUD_NAME, exists=True):
        mc.headsUpDisplay(_HUD_NAME, remove=True)


def hud_text() -> str:
    """What the HUD says. Maya calls this on every viewport refresh."""
    try:
        shape = stage_shape()
        stage = mayaUsd.ufe.getStage(shape) if shape else None
        if stage is None:
            return "no assembly stage in this scene"
        piece = open_piece(stage)
        if piece is None:
            return "nothing is open for editing"
        return f"{piece.GetName()} - {_layer_display(child_source_layer(piece))}"
    except AssemblyError as exc:
        # These refusals are already written for the artist, and their first
        # sentence is the whole story; the rest is advice for the dialog.
        return str(exc).split(". ")[0]
    except Exception:
        log.exception("The assembly edit-target HUD could not name a layer")
        return "unknown - see the Script Editor"


def _ufe_path(prim: Usd.Prim) -> str:
    shape = stage_shape()
    if shape is None:
        raise EditError(
            "This scene has no assembly stage, so it holds no piece to edit. Split "
            "a piece out of an assembly first."
        )
    return f"{shape},{prim.GetPath()}"


def _refuse_non_piece(stage: Usd.Stage, prim: Usd.Prim) -> None:
    """Only a piece prim is edited: mayaUsd would happily pull the assembly's root
    or a mesh inside a piece, and neither counts as open afterwards."""
    if prim.GetParent() != stage.GetDefaultPrim():
        raise EditError(
            f"'{prim.GetName()}' is not a piece of this assembly. Pick the piece "
            "itself in the assembly, not a group above it or a mesh inside it."
        )


def _release_materials(prim: Usd.Prim, child_layer: Sdf.Layer) -> None:
    """Clear the scene of shading groups named like the child's materials, so the
    pull brings them in under their own names. Deletes scene nodes."""
    in_use = delete_unused_shading_groups(_material_names(child_layer))
    if in_use:
        users = cast(list[str], mc.sets(in_use[0], query=True) or [])
        raise EditError(
            f"Material '{in_use[0]}' of '{prim.GetName()}' is still assigned to "
            f"{', '.join(users[:3])} in this scene, so opening the piece would "
            "bring its copy in under another name and its textures would no "
            "longer find it. Split that geometry or give it a different "
            "material, then open the piece again."
        )


def _material_names(layer: Sdf.Layer) -> list[str]:
    """The child's materials, which are the shading groups a pull creates."""
    names: list[str] = []

    def visit(path: Sdf.Path | str) -> None:
        spec = layer.GetPrimAtPath(path)
        if spec is not None and spec.typeName == _MATERIAL_TYPE:
            names.append(spec.name)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return names


def _refuse_moved(piece: Usd.Prim, maya_node: str) -> None:
    """A piece is placed in the assembly, not while it is open for editing."""
    placement = UsdGeom.Xformable(piece).GetLocalTransformation()
    # `mc.xform` is typed as the union of every shape its flags can return.
    values = cast(
        list[float], mc.xform(maya_node, query=True, matrix=True, objectSpace=True)
    )
    opened = Gf.Matrix4d(*values)
    moved = any(
        (opened.GetRow(row) - placement.GetRow(row)).GetLength() > _PLACEMENT_TOLERANCE
        for row in range(4)
    )
    if not moved:
        return
    translate, rotate, scale = _channel_values(placement)
    raise EditError(
        f"'{piece.GetName()}' was moved while open for editing, and a piece is "
        "placed by moving it in the assembly, not its open copy. Set the group's "
        f"Translate back to {translate}, Rotate to {rotate} and Scale to {scale} "
        "(or undo the move), then Save Piece again."
    )


def _channel_values(matrix: Gf.Matrix4d) -> tuple[str, str, str]:
    """`matrix` as Maya's channel box shows it: translate, rotate (degrees), scale."""
    transform = om.MTransformationMatrix(
        om.MMatrix([value for row in range(4) for value in matrix.GetRow(row)])
    )
    translate = transform.translation(om.MSpace.kTransform)
    rotate = transform.rotation()
    scale = transform.scale(om.MSpace.kTransform)
    return (
        _triple(translate.x, translate.y, translate.z),
        _triple(*(math.degrees(angle) for angle in (rotate.x, rotate.y, rotate.z))),
        _triple(*scale),
    )


def _triple(*values: float) -> str:
    return (
        "("
        + ", ".join(f"{value:.3f}".rstrip("0").rstrip(".") for value in values)
        + ")"
    )


def _refuse_material_problems(piece: Usd.Prim, maya_node: str) -> None:
    """The child layer is a model: it meets the same material rules as a split."""
    problems = material_problems([maya_node])
    if problems:
        raise EditError(
            f"Fix the materials on '{piece.GetName()}' before saving it. "
            + " ".join(f"{problem}." for problem in problems)
        )


def _child_edit_target(prim: Usd.Prim) -> Usd.EditTarget:
    """An edit target across the piece's one payload, into the child's own layer."""
    payloads = [
        arc
        for arc in Usd.PrimCompositionQuery(prim).GetCompositionArcs()
        if arc.GetArcType() == Pcp.ArcTypePayload
    ]
    if len(payloads) != 1:
        raise EditError(
            f"'{prim.GetName()}' is built from {len(payloads)} payloads and editing "
            "needs exactly one. Only a piece written by Split Assembly can be "
            "edited from the assembly."
        )
    payload = payloads[0]
    node = payload.GetTargetNode()
    return Usd.EditTarget(node.layerStack.identifier.rootLayer, node)


def _confirm_assembly_untouched(
    stage: Usd.Stage, prim: Usd.Prim, child_layer: Sdf.Layer, before: str
) -> None:
    """Prove the merge crossed the payload rather than landing in the assembly."""
    root_layer = stage.GetRootLayer()
    if root_layer.ExportToString() == before:
        return

    raise EditError(
        f"The edits to '{prim.GetName()}' changed the assembly instead of "
        f"{_layer_display(child_layer)}, which would hide them from every other "
        "department. Nothing is lost: undo to bring the piece back into Maya, then "
        f"tell a TD. {_assembly_change(root_layer, prim.GetPath())}"
    )


def _assembly_change(root_layer: Sdf.Layer, path: Sdf.Path) -> str:
    """Name what the assembly's layer gained, for the artist to quote to a TD."""
    spec = root_layer.GetPrimAtPath(path)
    if spec is None:
        return "The piece is no longer in the assembly's layer at all."

    gained = sorted(
        [prop.name for prop in spec.properties if not prop.name.startswith("xformOp")]
        + [child.name for child in spec.nameChildren]
    )
    if gained:
        return f"The assembly gained {', '.join(gained)}."
    return "The piece's placement in the assembly was overwritten."


def _save_child_layer(prim: Usd.Prim, child_layer: Sdf.Layer) -> None:
    """Write the merged edits to disk, or say plainly that they exist only in memory.

    By now the merge has deleted the Maya group, so the dirty layer is the only
    copy of the artist's work.
    """
    try:
        child_layer.Save()
    except Tf.ErrorException as exc:
        log.exception("Could not save %s after merging it", child_layer.identifier)
        raise EditError(
            f"The edits to '{prim.GetName()}' were merged but could not be written "
            f"to {_layer_display(child_layer)}. They exist only in this Maya "
            "session: leave Maya open and ask a TD to save that layer."
        ) from exc


def _layer_display(layer: Sdf.Layer) -> str:
    """A layer path as an artist reads it: relative to the production root."""
    return production_relative_identifier(Path(layer.realPath or layer.identifier))
