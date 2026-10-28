"""Wedge Send: the Wedge on Tractor button of a File Cache.

The run lives in the node's Send folder, and the node itself is left alone: a
wedge chooses values, and Review Wedges applies the chosen ones.
"""

from __future__ import annotations

import logging
import shutil
import traceback
from pathlib import Path

import hou
import tractor.api.author as author

from pipe.core.publish import Refused
from pipe.dcc.houdini import runtime
from pipe.dcc.houdini.publish import version
from pipe.dcc.houdini.tractor import (
    SendRefused,
    caches,
    commands,
    job,
    snapshot,
    wedge_dialog,
    wedges,
)
from pipe.dcc.houdini.tractor.cache_send import (
    BUILD_FAILED,
    check,
    note,
    refuse,
    spool,
    title,
)
from pipe.dcc.houdini.tractor.send import environment

# The line of a node's comment that names its last wedge run.
COMMENT = "Tractor wedges"

log = logging.getLogger(__name__)

# The rows last sent for each node by its session id, so the next round starts
# from them.
_last_rows: dict[int, list[wedge_dialog.Row]] = {}


def send(node: hou.Node) -> None:
    """Ask what to wedge. The dialog's Send spools the job."""
    try:
        check(node)
        publish = _publish()
    except SendRefused as refusal:
        refuse(str(refusal))
        return
    # The stubs say sessionId() returns None. It returns the node's id.
    key: int = node.sessionId()  # ty: ignore[invalid-assignment]
    start, end = (int(v) for v in node.evalParmTuple("f")[:2])
    # A cache wedged for the first time is often not wired to the shot yet.
    default_draw = wedges.SHOT if node in caches.Walk(publish) else wedges.CACHE
    wedge_dialog.show(
        runtime.get_main_qt_window(),
        node,
        lambda current: title(current, " wedges"),
        (start, end),
        default_draw,
        _last_rows.get(key, []),
        lambda parms, draw: _rebuilds(_node(key), publish, parms, draw),
        lambda choice: _submit(key, choice),
    )


def _submit(key: int, choice: wedge_dialog.WedgeChoice) -> None:
    """Spool one job that sims and flipbooks each wedge, or raise SendRefused
    before anything is written."""
    _last_rows[key] = choice.rows
    node = _node(key)
    check(node)
    publish = _publish()
    found = _wedges(choice.rows)
    parms = [_parm(path) for path in found[0].values]
    rebuilds = _rebuilds(node, publish, parms, choice.draw)
    try:
        built, run = _build(node, publish, choice, found, rebuilds)
    except SendRefused:
        # The dialog says why and stays open.
        raise
    except Exception:
        log.exception("Building the wedge job for %s failed", node.path())
        refuse(BUILD_FAILED, traceback.format_exc())
        return

    job_id = spool(built, run.title)
    if job_id is None:
        note(node, COMMENT, f"{COMMENT} {run.folder.name}: not confirmed")
        return
    # Review asks Tractor whether the job still runs before offering to delete it.
    (run.folder / wedges.JOB).write_text(f"{job_id}\n")
    what = f"flipbooks {len(run.wedges)} wedges of {node.name()}"
    if rebuilds == [node]:
        what = f"sims {len(run.wedges)} wedges of {node.name()}"
    elif rebuilds:
        names = ", ".join(cache.name() for cache in rebuilds)
        what = f"writes {names} for each of {len(run.wedges)} wedges of {node.name()}"
    hou.ui.displayMessage(
        f"Job {job_id} {what} into\n{run.folder}\n\n"
        "Press Review Wedges to play the finished ones.",
        title="Job sent to Tractor",
    )
    note(node, COMMENT, f"{COMMENT} {run.folder.name}: job {job_id}")


def _node(key: int) -> hou.Node:
    # The dialog stays open while the artist works, who may delete the node or
    # open another hip.
    node = hou.nodeBySessionId(key)
    if node is None:
        raise SendRefused(
            "The node is gone, or its hip was closed. Press Wedge on Tractor on "
            "it again."
        )
    return node


def _rebuilds(
    node: hou.Node, publish: hou.LopNode, parms: list[hou.Parm], draw: str
) -> list[hou.Node]:
    """What each wedge rebuilds, in cook order: every cache loading from disk
    between a wedged parm and what the flipbook draws, whose files would hide
    the change, and the node itself once a wedged parm reaches it. Raises
    SendRefused when no flipbook would show a wedge's change."""
    if not parms:
        return []
    owners = [parm.node() for parm in parms]
    drawn = caches.Walk(publish if draw == wedges.SHOT else node)
    # Only Shot can leave the node out of what is drawn.
    if node not in drawn and any(owner in caches.Walk(node) for owner in owners):
        raise SendRefused(
            f"{publish.path()} doesn't read {node.name()}, so the shot's "
            f"flipbooks wouldn't show its sims. Set Draw to Cache only, or "
            f"connect {node.name()} to the shot, then Send again."
        )
    if not any(owner in drawn for owner in owners):
        names = ", ".join(wedges.label(parm.path()) for parm in parms)
        raise SendRefused(
            f"Nothing the flipbook draws reads {names}, so every wedge would "
            "look the same.\n\nHoudini doesn't report a parm read by VEX, such "
            "as ch() in a wrangle. Give the wrangle a spare parm whose "
            f'expression is ch("{parms[0].path()}"), read that spare parm in '
            "the VEX, then Send again."
        )
    # A cache loading from disk stands in for everything behind it, so a
    # change behind it is drawn only once the cache is written again.
    return [
        reader
        for reader in drawn.readers(owners)
        if reader == node or (reader not in owners and caches.loads_from_disk(reader))
    ]


def _build(
    node: hou.Node,
    publish: hou.LopNode,
    choice: wedge_dialog.WedgeChoice,
    found: list[wedges.Wedge],
    rebuilds: list[hou.Node],
) -> tuple[author.Job, wedges.Run]:
    folder = snapshot.claim(node)
    try:
        hip = Path(hou.hipFile.path())
        run = wedges.Run(
            title=choice.title,
            snapshot=folder / f"{snapshot.SNAPSHOT}{hip.suffix}",
            hip=hip,
            node=node.path(),
            start=choice.start,
            end=choice.end,
            fps=hou.fps(),
            publish=publish.path(),
            draw=choice.draw,
            rebuilds=[_rebuild(cache, choice.end) for cache in rebuilds],
            wedges=found,
        )
        paths = [rebuild.path for rebuild in run.rebuilds]
        spec = commands.Wedges(
            wedges.write_record(run), environment(), wedges.chunks(run), paths
        )
        names = [wedge.name for wedge in run.wedges]
        # Made now, so Review's Delete tells a wedge yet to run from a deleted one.
        for name in names:
            (folder / name).mkdir()
        built = job.wedge_job(
            choice.title, choice.priority, choice.service, spec, names
        )
        snapshot.write(run.snapshot)
    except BaseException:
        # Nothing has read the folder yet.
        shutil.rmtree(folder)
        raise
    return built, run


def _rebuild(node: hou.Node, end: int) -> wedges.Rebuild:
    """The cache's own frames, up to the run's last."""
    chunks = caches.chunks(node)
    return wedges.Rebuild(node.path(), chunks[0][0], min(chunks[-1][1], float(end)))


def _publish() -> hou.LopNode:
    """The hip's publish node, whose stage every flipbook looks through the
    shot camera of."""
    try:
        publish = version.publish_node()
    except Refused as refusal:
        raise SendRefused(str(refusal)) from None
    stage = publish.stage()
    if stage is None:
        errors = "\n".join(publish.errors())
        raise SendRefused(
            f"The stage at {publish.path()} doesn't cook, so there is nothing to "
            f"flipbook the wedges with. Fix its errors, then Send again.\n\n{errors}"
        )
    if not stage.GetPrimAtPath(wedges.SHOT_CAMERA):
        raise SendRefused(
            f"The stage at {publish.path()} has no {wedges.SHOT_CAMERA} to flipbook "
            "the wedges through. Load the shot camera, then Send again."
        )
    return publish


def _wedges(rows: list[wedge_dialog.Row]) -> list[wedges.Wedge]:
    """Check what the dialog's rows wedge, and make one wedge per combination."""
    parsed: list[tuple[str, list[wedges.Value]]] = []
    for path, text in rows:
        parm = _parm(path)
        if parm.path() in (done for done, _ in parsed):
            raise SendRefused(f"{wedges.label(parm.path())} is wedged twice.")
        try:
            values = wedges.parse(parm, text)
        except ValueError as error:
            raise SendRefused(str(error)) from None
        parsed.append((parm.path(), values))
    return wedges.combine(parsed)


def _parm(path: str) -> hou.Parm:
    if not path:
        raise SendRefused("Choose the parm each row of values is for.")
    parm = hou.parm(path)
    if parm is not None:
        if parm.node().isInsideLockedHDA():
            raise SendRefused(f"{path} is inside a locked asset, so it can't be set.")
        if parm.isLocked():
            raise SendRefused(f"{path} is locked. Unlock it, then Send again.")
        return parm
    parms = hou.parmTuple(path)
    if parms is not None:
        raise SendRefused(
            f"{path} has {len(parms)} parts. Wedge one of them, such as "
            f"{parms[0].name()}."
        )
    raise SendRefused(f"There is no parm {path}.")
