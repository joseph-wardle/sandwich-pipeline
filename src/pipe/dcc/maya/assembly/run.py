"""Split Pieces

A split cannot be undone, so the scene is saved and a model version kept
before the first piece, and the scene is saved again after every piece.
Whatever was split when something fails is on disk, and the next plan shows
only what is left.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from maya import cmds as mc

from pipe.core.asset import asset_owner_for, maya_model_stream, paths_for_asset
from pipe.core.asset.paths import asset_root
from pipe.core.assembly.model import AssemblyError, PieceTarget, SplitResult
from pipe.core.assembly.plan import Plan, Row, register_child
from pipe.core.shotgrid import Asset, ShotGrid
from pipe.core.versioning import save_version
from pipe.dcc.maya.assembly.split import split_piece

log = logging.getLogger(__name__)

VERSION_TITLE = "Before Split Pieces"


@dataclass(frozen=True)
class Failure:
    """The piece the run stopped at, and why, in the artist's terms."""

    group: str
    reason: str


@dataclass(frozen=True)
class RunReport:
    version: int | None
    split: tuple[SplitResult, ...]
    failure: Failure | None


def run_split(
    conn: ShotGrid,
    assembly: Asset,
    plan: Plan,
    *,
    on_piece: Callable[[Row], None] | None = None,
) -> RunReport:
    """Write `plan` into the scene, ShotGrid and the children's publish folders.

    `on_piece` is told each row as its split begins, for a progress bar.
    """
    if not plan.ready:
        raise AssemblyError(
            "Fix what the plan refuses, then refresh it before splitting."
        )
    scene = _scene_path()
    if scene is None:
        raise AssemblyError(
            "Save the scene first: a split keeps a version of the saved scene, "
            "and this one is untitled."
        )
    mc.file(save=True, force=True)
    paths = paths_for_asset(assembly)
    record = save_version(
        scene,
        maya_model_stream(paths, owner=asset_owner_for(assembly)),
        title=VERSION_TITLE,
    )

    split: list[SplitResult] = []
    failure: Failure | None = None
    try:
        for row, target in _register_targets(conn, plan):
            if on_piece is not None:
                on_piece(row)
            try:
                split.append(split_piece(row.piece, target, assembly_root=paths.root))
            except AssemblyError as error:
                log.exception("Split Pieces stopped at '%s'.", row.group)
                failure = Failure(row.group, str(error))
                break
            mc.file(save=True, force=True)
    finally:
        if split:
            mc.flushUndo()
    return RunReport(record.version, tuple(split), failure)


def _register_targets(conn: ShotGrid, plan: Plan) -> Iterator[tuple[Row, PieceTarget]]:
    """Each row with its target, registering a child in ShotGrid (and making its
    folder) as its first row is reached, so a child after the stop is untouched."""
    for child in plan.children:
        asset = register_child(conn, child)
        root = asset_root(asset)
        root.mkdir(mode=0o770, parents=True, exist_ok=True)
        for row in child.rows:
            yield row, PieceTarget(asset.name, root, row.variant)


def _scene_path() -> Path | None:
    raw = mc.file(query=True, sceneName=True)
    return Path(raw) if isinstance(raw, str) and raw else None
