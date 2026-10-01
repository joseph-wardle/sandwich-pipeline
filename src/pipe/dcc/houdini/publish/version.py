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

from pipe.core.announce import announce_publish
from pipe.core.publish import (
    commit_version,
    copy_source,
    create_staging,
    discard_staged,
    make_current,
    next_version,
    stamp,
    staging_layer_path,
)
from pipe.core.sets import PUBLISHED_FILE_NAME, SETS_DIRNAME, prepare_layer
from pipe.core.sets import current_layer_path as set_layer_path
from pipe.core.shot import current_layer_path as shot_layer_path
from pipe.core.shot import published_file_code, shot_root_path
from pipe.core.shotgrid import Set, Shot, ShotGrid, ShotGridError, ShotGridNotFound
from pipe.core.struct.timeline import Timeline
from pipe.core.ui import MessageDialog, PublishChoice, prompt_publish
from pipe.core.util.paths import get_production_path
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.houdini import runtime
from pipe.dcc.houdini.hipfile.departments import PUBLISHING_DEPARTMENTS
from pipe.dcc.houdini.hipfile.paths import current_hip_path
from pipe.dcc.houdini.util import nodetypes

log = logging.getLogger(__name__)

TITLE = "Publish"
SHOTS_DIRNAME = "shot"
# The USD ROP inside the publish node.
ROP_NAME = "rop"

_NOT_A_PUBLISHING_HIP = (
    "This hip isn't in a shot department's folder or a set's folder, so there is "
    "nothing for it to publish. Open it with Open Shot or Open Set."
)


class _Refused(Exception):
    """The publish can't go on. The message tells the artist why and what to do."""


@dataclass(frozen=True)
class _Target:
    """What a hip publishes, worked out from where the hip is."""

    entity: Shot | Set
    # How the dialogs name this version, such as `A_210 cfx v005`.
    label: str
    current: Path
    version: int
    frame_range: tuple[int, int]
    # The ShotGrid PublishedFile's name, the same on every version, and its code.
    file_name: str
    file_code: str
    # Whose downstream is told. None tells no one.
    department: str | None


def publish() -> None:
    """Publish the next version of what this hip's publish node declares."""
    window = runtime.get_main_qt_window()
    try:
        lines = _publish(window)
    except _Refused as refusal:
        MessageDialog(window, str(refusal), TITLE).exec_()
        return
    if lines:
        MessageDialog(window, "\n".join(lines), TITLE).exec_()


def _publish(window: QtWidgets.QWidget | None) -> list[str]:
    """Returns the lines of the result, or none if the artist backed out."""
    hip_path = current_hip_path()
    if hip_path is None:
        raise _Refused(_NOT_A_PUBLISHING_HIP)
    node = _publish_node()
    conn = ShotGrid.connect(DB_Config)
    target = _target(conn, hip_path)

    choice = prompt_publish(window, target.label)
    if choice is None:
        return []
    author = resolve_artist_display_name()
    if not _write_staged(node, target, hip_path, choice, author):
        return []

    staging = staging_layer_path(target.current, target.version).parent
    try:
        version_path = commit_version(target.current, target.version)
    except OSError:
        log.exception("Could not rename %s into place.", staging)
        raise _Refused(
            f"{target.label} was written to {staging} but couldn't be moved into "
            "place, so nothing was published. Ask a TD."
        ) from None
    try:
        make_current(target.current, target.version)
    except OSError:
        log.exception("Could not make %s current.", target.label)
        raise _Refused(
            f"{target.label} is published, but it isn't current, so nothing reads "
            "it yet. A TD can run pipe.core.publish.make_current on "
            f"{target.current} with version {target.version}."
        ) from None

    lines = [f"Published {target.label}."]
    try:
        conn.create_published_file(
            target.entity,
            name=target.file_name,
            code=target.file_code,
            version=target.version,
            path=version_path,
            description=choice.note,
        )
    except ShotGridError:
        log.exception("Could not register %s in ShotGrid.", target.label)
        lines.append("ShotGrid: the version wasn't registered. Tell a TD.")
    if target.department is not None:
        version_name = version_path.parent.name
        lines += announce_publish(
            conn,
            deliverable=cast(Shot, target.entity),
            department=target.department,
            artist=author,
            path=version_path,
            detail=f"{version_name}: {choice.note}" if choice.note else version_name,
        )
    return lines


def _publish_node() -> hou.LopNode:
    node_type = cast(
        hou.NodeType, hou.nodeType(hou.lopNodeTypeCategory(), nodetypes.PUBLISH)
    )
    nodes = node_type.instances()
    if not nodes:
        raise _Refused(
            f"This hip has no {node_type.description()} node. Add one below the "
            "last node of the work to publish, then publish again."
        )
    if len(nodes) > 1:
        paths = "\n".join(f"  {node.path()}" for node in nodes)
        raise _Refused(
            f"This hip has {len(nodes)} {node_type.description()} nodes, and a hip "
            f"publishes one layer:\n{paths}\nDelete all but one, then publish again."
        )
    return cast(hou.LopNode, nodes[0])


def _target(conn: ShotGrid, hip_path: Path) -> _Target:
    try:
        parts = hip_path.relative_to(get_production_path().resolve()).parts
    except ValueError:
        parts = ()
    try:
        if len(parts) == 3 and parts[0] == SETS_DIRNAME:
            return _set_target(conn.get_set(name=parts[1]))
        if len(parts) == 4 and parts[0] == SHOTS_DIRNAME:
            department = parts[2]
            if department not in PUBLISHING_DEPARTMENTS:
                raise _Refused(f"A {department} hip doesn't publish.")
            return _shot_target(conn.get_shot(code=parts[1]), department)
    except ShotGridNotFound:
        raise _Refused(
            f"ShotGrid has no {parts[0]} named {parts[1]}, so nothing was published. "
            "Ask production to add it, or a TD if it is there."
        ) from None
    except ShotGridError as exc:
        log.exception("Could not look up what %s publishes.", hip_path)
        raise _Refused(
            "ShotGrid couldn't say what this hip publishes, so nothing was "
            f"published.\n{exc}"
        ) from None
    raise _Refused(_NOT_A_PUBLISHING_HIP)


def _shot_target(shot: Shot, department: str) -> _Target:
    code = cast(str, shot.code)
    current = shot_layer_path(shot_root_path(shot), department)
    version = next_version(current)
    timeline = Timeline.from_shot(shot)
    return _Target(
        entity=shot,
        label=f"{code} {department} v{version:03d}",
        current=current,
        version=version,
        frame_range=(timeline.start, timeline.end),
        file_name=department,
        file_code=published_file_code(code, department, version),
        department=department,
    )


def _set_target(set: Set) -> _Target:
    current = set_layer_path(set.name)
    version = next_version(current)
    # A set doesn't move, so one frame holds all of it.
    frame = hou.intFrame()
    return _Target(
        entity=set,
        label=f"{set.display_name} v{version:03d}",
        current=current,
        version=version,
        frame_range=(frame, frame),
        file_name=PUBLISHED_FILE_NAME,
        file_code=f"{set.name}_v{version:03d}",
        department=None,
    )


def _write_staged(
    node: hou.LopNode,
    target: _Target,
    hip_path: Path,
    choice: PublishChoice,
    author: str,
) -> bool:
    """Write the version into its staging folder. False if the artist backed out."""
    try:
        # Saved first, so the scene kept with the version is the one that made it.
        hou.hipFile.save()
    except hou.OperationFailed as exc:
        raise _Refused(
            f"The hip couldn't be saved, so nothing was published.\n"
            f"{exc.instanceMessage()}"
        ) from None
    staged = _create_staging(target)
    try:
        _render(node, staged, target.frame_range)
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


def _create_staging(target: _Target) -> Path:
    try:
        return create_staging(target.current, target.version)
    except FileExistsError as exc:
        raise _Refused(
            f"Nothing was published: {exc.filename} already exists. Someone may be "
            f"publishing {target.label} right now. If not, a publish stopped "
            "partway: delete that folder and publish again."
        ) from None
    except OSError:
        log.exception("Could not create the staging folder for %s.", target.label)
        raise _Refused(
            f"Couldn't create a folder for {target.label}, so nothing was "
            f"published. Ask a TD to check the permissions on {target.current.parent}."
        ) from None


def _render(node: hou.LopNode, staged: Path, frame_range: tuple[int, int]) -> None:
    rop = cast(hou.RopNode, node.node(ROP_NAME))
    try:
        rop.render(frame_range=frame_range, output_file=str(staged))
    except hou.Error as exc:
        log.exception("%s failed to write %s.", node.path(), staged)
        raise _Refused(
            f"Nothing was published. {node.path()} failed:\n{exc.instanceMessage()}"
        ) from None
    if not staged.is_file():
        raise _Refused(
            f"Nothing was published. {node.path()} wrote no file. Check the errors "
            "on the nodes above it."
        )


def _prepare_set(staged: Path, set: Set, hip_path: Path) -> bool:
    """Author the set contract on the staged layer. False if the artist backed out."""
    try:
        stray = prepare_layer(staged, set.name, hip_path)
    except ValueError:
        raise _Refused(
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
