"""Split Pieces

A split cannot be undone, so the scene is saved and a model version kept
before the first piece, and the scene is saved again after every piece.
Whatever was split when something fails is on disk, and the next plan shows
only what is left.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from maya import cmds as mc

from pipe.core.asset import asset_owner_for, maya_model_stream, paths_for_asset
from pipe.core.asset.paths import asset_root
from pipe.core.assembly.model import AssemblyError, Piece, PieceTarget
from pipe.core.assembly.plan import Child, Plan, Refusal, register_child
from pipe.core.shotgrid import Asset, ShotGrid
from pipe.core.versioning import save_version
from pipe.dcc.maya.assembly.split import split_piece

log = logging.getLogger(__name__)

VERSION_TITLE = "Before Split Pieces"


@dataclass(frozen=True)
class RunReport:
    """How many pieces a run split, and the piece it stopped at, if one stopped it."""

    version: int | None
    split: int
    stop: Refusal | None


def run_split(
    conn: ShotGrid,
    assembly: Asset,
    plan: Plan,
    *,
    on_piece: Callable[[Piece], None] | None = None,
) -> RunReport:
    """Write `plan` into the scene, ShotGrid and the children's publish folders.

    `on_piece` is told each piece as its split begins, for a progress bar. The
    caller runs only a plan that is `ready`.
    """
    scene = _scene_path()
    if scene is None:
        raise AssemblyError(
            "Save the scene first: a split keeps a version of the saved scene, "
            "and this one is untitled."
        )
    try:
        mc.file(save=True, force=True)
    except RuntimeError as error:
        raise AssemblyError(
            f"The scene could not be saved, so nothing was split. Maya said: {error}"
        ) from error
    paths = paths_for_asset(assembly)
    record = save_version(
        scene,
        maya_model_stream(paths, owner=asset_owner_for(assembly)),
        title=VERSION_TITLE,
    )

    split: list[Piece] = []
    stop: Refusal | None = None
    try:
        for child in plan.children:
            stop = _split_child(conn, child, paths.root, split, on_piece)
            if stop is not None:
                break
    finally:
        if split:
            mc.flushUndo()
    return RunReport(record.version, len(split), stop)


def _split_child(
    conn: ShotGrid,
    child: Child,
    assembly_root: Path,
    split: list[Piece],
    on_piece: Callable[[Piece], None] | None,
) -> Refusal | None:
    """Register `child` in ShotGrid (and make its folder) as it is reached, then
    split its pieces in turn, appending each to `split` and stopping at the first
    failure. A child after the stop is untouched."""
    for piece in child.pieces:
        if not mc.objExists(piece.node):
            return Refusal(
                piece.name,
                f"'{piece.name}' is no longer in the scene, so the plan is out of "
                "date. Refresh the plan, then split again.",
            )
    try:
        asset = register_child(conn, child)
        root = asset_root(asset)
        root.mkdir(mode=0o770, parents=True, exist_ok=True)
    except Exception as error:
        log.exception("Split Pieces could not register '%s'.", child.display_name)
        return Refusal(
            child.pieces[0].name,
            f"'{child.display_name}' could not be registered, so none of its pieces "
            f"was split: {error}",
        )
    for piece in child.pieces:
        if on_piece is not None:
            on_piece(piece)
        stop = _split_and_save(
            piece, PieceTarget(asset.name, root, piece.variant), assembly_root
        )
        if stop is not None:
            return stop
        split.append(piece)
    return None


def _split_and_save(
    piece: Piece, target: PieceTarget, assembly_root: Path
) -> Refusal | None:
    """Split one piece and save the scene, naming what stopped either.

    A failed save matters most: the split is done in memory and on disk, and
    the scene is the only record that the group is gone.
    """
    try:
        split_piece(piece, target, assembly_root=assembly_root)
    except AssemblyError as error:
        log.exception("Split Pieces stopped at '%s'.", piece.name)
        return Refusal(piece.name, str(error))
    except Exception:
        log.exception("Split Pieces stopped at '%s' unexpectedly.", piece.name)
        return Refusal(
            piece.name,
            f"'{piece.name}' could not be split for a reason the tool did not "
            "expect; the Script Editor has the details. Reopen the scene without "
            "saving it, then ask a TD before splitting again.",
        )
    try:
        mc.file(save=True, force=True)
    except Exception:
        log.exception("The scene could not be saved after splitting '%s'.", piece.name)
        return Refusal(
            piece.name,
            f"'{piece.name}' is split, but the scene could not be saved afterwards; "
            "the Script Editor has the details. Save the scene by hand (File > "
            "Save) before doing anything else, or the piece comes back unsplit "
            "beside its new asset.",
        )
    return None


def _scene_path() -> Path | None:
    raw = mc.file(query=True, sceneName=True)
    return Path(raw) if isinstance(raw, str) and raw else None
