"""Wedge review: the Review Wedges button of a File Cache. Every finished wedge
of the node's runs plays in the playblast viewer, newest run first. Apply
Values sets the wedge on screen in the hip, and Delete removes it from disk."""

from __future__ import annotations

import shutil
from pathlib import Path

import hou

from pipe.core.playblast import PreviewClip
from pipe.core.playblast.viewer import ClipAction, open_viewer
from pipe.dcc.houdini import runtime
from pipe.dcc.houdini.tractor import engine, snapshot, wedges

TITLE = "Review Wedges"
MISSING_FRAMES = (
    "The wedge may have been deleted, or its Draw retried in Tractor, since "
    "Review Wedges opened. Close it and press Review Wedges again."
)


def review(node: hou.Node) -> None:
    runs, unread = _runs(node)
    if not runs and not unread:
        hou.ui.displayMessage(
            f"No wedge run in {snapshot.folder(node)} was sent from {node.path()}. A node "
            "renamed or moved since leaves its runs behind. Wedge on Tractor "
            "sends one.",
            title=TITLE,
        )
        return
    clips: list[PreviewClip] = []
    # The clips of each run, which the grid plays side by side.
    grids: list[list[PreviewClip]] = []
    # The run and wedge of each clip, by its frames folder.
    shown: dict[Path, tuple[wedges.Run, wedges.Wedge]] = {}
    for run in runs:
        grids.append([])
        for wedge in run.wedges:
            frames = run.folder / wedge.name / wedges.FLIPBOOK
            # The blade renames the folder into place once every frame is burned.
            if not frames.is_dir():
                continue
            clip = PreviewClip(
                label=f"{run.folder.name} {wedge.name}: "
                + ", ".join(wedges.describe(wedge)),
                frames_dir=frames,
                frames_basename=wedges.FRAMES,
                frame_start=run.start,
                frame_end=run.end,
                fps=round(run.fps),
            )
            clips.append(clip)
            grids[-1].append(clip)
            shown[frames] = (run, wedge)
    finished = {run.folder for run, _ in shown.values()}
    unfinished = [run for run in runs if run.folder not in finished]
    if unfinished or unread:
        _offer_delete(node, unfinished, unread)
    # The offer said why there is nothing to play.
    if not clips:
        return
    actions = [
        ClipAction(
            "Apply Values",
            lambda clip: _apply(*shown[clip.frames_dir]),
            "Set this wedge's values in the hip.",
        ),
        ClipAction(
            "Delete",
            lambda clip: _delete(*shown[clip.frames_dir]),
            "Delete this wedge's cache and flipbook from disk.",
        ),
    ]
    open_viewer(
        clips,
        runtime.get_main_qt_window(),
        actions=actions,
        missing_frames=MISSING_FRAMES,
        runs=grids,
    )


def _runs(node: hou.Node) -> tuple[list[wedges.Run], list[tuple[Path, str]]]:
    """The node's wedge runs, newest first, and the folder of each record that
    can't be read, with why."""
    records = sorted(snapshot.folder(node).glob(f"v*/{wedges.RECORD}"), reverse=True)
    runs = []
    unread = []
    for record in records:
        try:
            run = wedges.read_record(record)
        except ValueError as error:
            unread.append((record.parent, str(error)))
            continue
        # Two nodes of one name share a Send folder.
        if run.node == node.path():
            runs.append(run)
    return runs, unread


def _offer_delete(
    node: hou.Node, unfinished: list[wedges.Run], unread: list[tuple[Path, str]]
) -> None:
    """Offer to delete the runs that won't finish. A run whose job is still in
    Tractor is only listed, since deleting it fails the job."""
    folders = [run.folder for run in unfinished] + [folder for folder, _ in unread]
    running, unknown, unreachable = _running(folders)
    groups = []
    stopped = [run for run in unfinished if run.folder not in running]
    if stopped:
        groups.append(
            [f"These runs of {node.path()} have no finished wedge:"]
            + [f'{run.folder}, job "{run.title}"' for run in stopped]
        )
    unread = [(folder, why) for folder, why in unread if folder not in running]
    if unread:
        groups.append(
            ["Review Wedges leaves out the runs it can't read:"]
            + [f"{folder}: {why}" for folder, why in unread]
        )
    if running:
        groups.append(
            ["These runs' jobs are still in Tractor, so they are kept:"]
            + [f"{folder}: job {job_id}" for folder, job_id in running.items()]
        )
    text = "\n\n".join("\n".join(group) for group in groups)
    folders = [folder for folder in folders if folder not in running]
    if not folders:
        hou.ui.displayMessage(text, title=TITLE)
        return
    if unreachable:
        text += (
            f"\n\nTractor can't be reached ({unreachable}), so Review Wedges "
            "can't tell whether their jobs still run, and deleting a run whose "
            "job runs fails the job. Check Tractor before deleting."
        )
    elif unknown:
        text += (
            "\n\nA run whose job is still in Tractor may yet finish, and deleting "
            "it fails the job. Check Tractor before deleting."
        )
    pressed = hou.ui.displayMessage(
        text,
        buttons=("Delete", "Keep"),
        default_choice=1,
        close_choice=1,
        title=TITLE,
        severity=hou.severityType.Warning if unknown else hou.severityType.Message,
    )
    if pressed != 0:
        return
    for folder in folders:
        shutil.rmtree(folder)
    hou.ui.setStatusMessage(f"Deleted {', '.join(f.name for f in folders)}")


def _running(folders: list[Path]) -> tuple[dict[Path, int], list[Path], str]:
    """The job of each folder that is still in Tractor, the folders whose job
    can't be told, and why Tractor couldn't be asked, if it couldn't."""
    running: dict[Path, int] = {}
    unknown: list[Path] = []
    unreachable = ""
    for folder in folders:
        path = folder / wedges.JOB
        # Runs sent before Send wrote it, and jobs Tractor didn't confirm, have
        # none. Once Tractor can't be reached, it isn't asked again.
        if not path.is_file() or unreachable:
            unknown.append(folder)
            continue
        job_id = int(path.read_text())
        try:
            if not engine.ended(job_id):
                running[folder] = job_id
        except (OSError, ValueError) as error:
            # A URLError wraps what went wrong in <urlopen error ...>.
            unreachable = str(getattr(error, "reason", error))
            unknown.append(folder)
    return running, unknown, unreachable


def _apply(run: wedges.Run, wedge: wedges.Wedge) -> bool:
    name = f"{run.folder.name} {wedge.name}"
    parms = [parm for path in wedge.values if (parm := hou.parm(path)) is not None]
    locked = [parm.path() for parm in parms if parm.isLocked()]
    if locked:
        hou.ui.displayMessage(
            f"{', '.join(locked)} is locked, so nothing of {name} was set. "
            "Unlock it, then Apply again.",
            title=TITLE,
            severity=hou.severityType.Warning,
        )
        return False
    lost = [f"{parm.path()}: {_held(parm)}" for parm in parms if parm.keyframes()]
    if lost:
        pressed = hou.ui.displayMessage(
            f"Applying {name} replaces what these parms hold:\n" + "\n".join(lost),
            buttons=("Apply", "Cancel"),
            default_choice=1,
            close_choice=1,
            title=TITLE,
            severity=hou.severityType.Warning,
        )
        if pressed != 0:
            return False
    with hou.undos.group(f"Apply wedge {name}"):
        missing = wedges.set_values(wedge)
    if missing:
        done = [
            line
            for path, line in zip(wedge.values, wedges.describe(wedge), strict=True)
            if path not in missing
        ]
        hou.ui.displayMessage(
            f"The hip no longer has {', '.join(missing)}, so those values of "
            f"{name} were not set."
            + (f" It did set {', '.join(done)}." if done else ""),
            title=TITLE,
            severity=hou.severityType.Warning,
        )
        return False
    hou.ui.setStatusMessage(
        f"Applied {name}: {', '.join(wedges.describe(wedge))}. "
        "Save to Disk to cache it."
    )
    return False


def _held(parm: hou.Parm) -> str:
    keys = parm.keyframes()
    if len(keys) > 1:
        return f"{len(keys)} keyframes"
    # A channel reference is an expression too, such as ch("../tx").
    return keys[0].expression() if keys[0].isExpressionSet() else "a keyframe"


def _delete(run: wedges.Run, wedge: wedges.Wedge) -> bool:
    name = f"{run.folder.name} {wedge.name}"
    # Deleted from another viewer of the same runs.
    if not (run.folder / wedge.name).exists():
        hou.ui.setStatusMessage(f"{name} was already deleted")
        return True
    pressed = hou.ui.displayMessage(
        f"Delete {name}? Its cache and flipbook can't be recovered.",
        buttons=("Delete", "Cancel"),
        default_choice=1,
        close_choice=1,
        title=TITLE,
        severity=hou.severityType.Warning,
    )
    if pressed != 0:
        return False
    shutil.rmtree(run.folder / wedge.name)
    # Send makes every wedge's folder, so none left means none is still to
    # run, and the run's snapshot and record go with the last one.
    if not any((run.folder / w.name).exists() for w in run.wedges):
        shutil.rmtree(run.folder)
    hou.ui.setStatusMessage(f"Deleted {name}")
    return True
