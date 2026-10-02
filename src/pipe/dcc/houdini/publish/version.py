"""Publish what a shot or set hip's publish node declares, as the next publish version.

The publish node's button and the shelf's Publish both call `publish`. Where the
version goes, its number and who is told come from where the hip is, never from
anything saved on the node (ADR-0029).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import hou
from env_sg import DB_Config
from Qt import QtWidgets

from pipe.core.publish import (
    Refused,
    Target,
    copy_source,
    discard_staged,
    release,
    stage,
    stamp,
)
from pipe.core.sets import SETS_DIRNAME, prepare_layer, set_target
from pipe.core.shot import shot_target
from pipe.core.shotgrid import Set, Shot, ShotGrid, ShotGridError, ShotGridNotFound
from pipe.core.struct.timeline import Timeline
from pipe.core.ui import MessageDialog, PublishChoice, prompt_publish
from pipe.core.util.paths import get_production_path
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.houdini import runtime
from pipe.dcc.houdini.hipfile.departments import PUBLISHING_DEPARTMENTS
from pipe.dcc.houdini.hipfile.paths import current_hip_path
from pipe.dcc.houdini.playblast import launch_playblast
from pipe.dcc.houdini.util import nodetypes

log = logging.getLogger(__name__)

TITLE = "Publish"
SHOTS_DIRNAME = "shot"
# The USD ROP inside the publish node.
ROP_NAME = "rop"

NOT_A_PUBLISHING_HIP = (
    "This hip isn't in a shot department's folder or a set's folder, so there is "
    "nothing for it to publish. Open it with Open Shot or Open Set."
)


@dataclass(frozen=True)
class _Published:
    # What the result box says, one line each.
    lines: list[str]
    # The description a playblast of this version starts with. None when the
    # artist didn't ask for a playblast.
    playblast: str | None


def publish() -> None:
    """Publish the next version of what this hip's publish node declares."""
    window = runtime.get_main_qt_window()
    try:
        published = _publish(window)
    except Refused as refusal:
        MessageDialog(window, str(refusal), TITLE).exec_()
        return
    if published is None:
        return
    MessageDialog(window, "\n".join(published.lines), TITLE).exec_()
    if published.playblast is not None:
        launch_playblast(published.playblast)


def _publish(window: QtWidgets.QWidget | None) -> _Published | None:
    """None if the artist backed out."""
    hip_path = current_hip_path()
    if hip_path is None:
        raise Refused(NOT_A_PUBLISHING_HIP)
    conn = ShotGrid.connect(DB_Config)
    target = hip_target(conn, hip_path)
    frame_range = _frame_range(target.entity)
    node = _publish_node()

    choice = prompt_publish(window, target.label)
    if choice is None:
        return None
    author = resolve_artist_display_name()
    if not _write_staged(node, target, frame_range, hip_path, choice, author):
        return None

    lines = release(conn, target, author=author, note=choice.note)
    description = f"{target.label}: {choice.note}" if choice.note else target.label
    return _Published(lines, description if choice.playblast else None)


def _publish_node() -> hou.LopNode:
    node_type = cast(
        hou.NodeType, hou.nodeType(hou.lopNodeTypeCategory(), nodetypes.PUBLISH)
    )
    nodes = node_type.instances()
    if not nodes:
        raise Refused(
            f"This hip has no {node_type.description()} node. Add one below the "
            "last node of the work to publish, then publish again."
        )
    if len(nodes) > 1:
        paths = "\n".join(f"  {node.path()}" for node in nodes)
        raise Refused(
            f"This hip has {len(nodes)} {node_type.description()} nodes, and a hip "
            f"publishes one layer:\n{paths}\nDelete all but one, then publish again."
        )
    return cast(hou.LopNode, nodes[0])


def hip_target(conn: ShotGrid, hip_path: Path) -> Target:
    """What the hip at `hip_path` publishes, which is decided by its folder."""
    try:
        parts = hip_path.relative_to(get_production_path().resolve()).parts
    except ValueError:
        parts = ()
    try:
        if len(parts) == 3 and parts[0] == SETS_DIRNAME:
            return set_target(conn.get_set(name=parts[1]))
        if len(parts) == 4 and parts[0] == SHOTS_DIRNAME:
            department = parts[2]
            if department not in PUBLISHING_DEPARTMENTS:
                raise Refused(f"A {department} hip doesn't publish.")
            return shot_target(conn.get_shot(code=parts[1]), department)
    except ShotGridNotFound:
        raise Refused(
            f"ShotGrid has no {parts[0]} named {parts[1]}. Ask production to add "
            "it, or a TD if it is there."
        ) from None
    except ShotGridError as exc:
        log.exception("Could not look up what %s publishes.", hip_path)
        raise Refused(
            f"ShotGrid couldn't say what this hip publishes.\n{exc}"
        ) from None
    raise Refused(NOT_A_PUBLISHING_HIP)


def _frame_range(entity: Shot | Set) -> tuple[int, int]:
    if isinstance(entity, Set):
        # A set doesn't move, so one frame holds all of it.
        frame = hou.intFrame()
        return frame, frame
    try:
        timeline = Timeline.from_shot(entity)
    except ShotGridError as exc:
        raise Refused(
            f"Nothing was published. {exc} Ask production to fill them in."
        ) from None
    return timeline.start, timeline.end


def _write_staged(
    node: hou.LopNode,
    target: Target,
    frame_range: tuple[int, int],
    hip_path: Path,
    choice: PublishChoice,
    author: str,
) -> bool:
    """Write the version into its staging folder. False if the artist backed out."""
    try:
        # Saved first, so the scene kept with the version is the one that made it.
        hou.hipFile.save()
    except hou.OperationFailed as exc:
        raise Refused(
            f"The hip couldn't be saved, so nothing was published.\n"
            f"{exc.instanceMessage()}"
        ) from None
    staged = stage(target)
    try:
        _render(node, staged, frame_range)
        if isinstance(target.entity, Set) and not _prepare_set(
            staged, target.entity, hip_path
        ):
            discard_staged(target.current, target.version)
            return False
        copy_source(target.current, target.version, hip_path)
        stamp(target.current, target.version, author=author, note=choice.note)
    except BaseException:
        discard_staged(target.current, target.version)
        raise
    return True


def _render(node: hou.LopNode, staged: Path, frame_range: tuple[int, int]) -> None:
    rop = cast(hou.RopNode, node.node(ROP_NAME))
    try:
        rop.render(frame_range=frame_range, output_file=str(staged))
    except hou.Error as exc:
        log.exception("%s failed to write %s.", node.path(), staged)
        raise Refused(
            f"Nothing was published. {node.path()} failed:\n{exc.instanceMessage()}"
        ) from None
    if not staged.is_file():
        raise Refused(
            f"Nothing was published. {node.path()} wrote no file. Check the errors "
            "on the nodes above it."
        )


def _prepare_set(staged: Path, set: Set, hip_path: Path) -> bool:
    """Author the set contract on the staged layer. False if the artist backed out."""
    try:
        stray = prepare_layer(staged, set.name, hip_path)
    except ValueError:
        raise Refused(
            f"Nothing was published: the stage has no /{set.name} prim. Put "
            f"everything the set needs under /{set.name} and publish again."
        ) from None
    if not stray:
        return True
    listed = "\n".join(f"  /{prim}" for prim in stray)
    choice = hou.ui.displayMessage(
        f"These prims are outside /{set.name} and won't reach shots:\n{listed}\n\n"
        "Publish anyway?",
        buttons=("Publish", "Cancel"),
        severity=hou.severityType.Warning,
        default_choice=1,
        close_choice=1,
        title=TITLE,
    )
    return choice == 0
