"""Version folders: each Send renders into a `v###` folder no other Send has used.

Configure's Output Folder shows the exact folder the next Send claims. Send
claims it with an exclusive `mkdir`, and when that folder is taken or no longer
the next free version it refuses and shows the next one rather than silently
taking another.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import hou

from pipe.core.cache import RENDER_DIRNAME, link_to_cache
from pipe.core.render import next_version
from pipe.core.util.paths import get_production_path
from pipe.dcc.houdini.hipfile.departments import Department
from pipe.dcc.houdini.tractor import SendRefused
from pipe.dcc.houdini.tractor.parms import LAST_JOB, LAYER, OUTPUT, ROOT, text


def _holder(node: hou.Node, name: str) -> hou.Parm | None:
    """The parm Send sets for `name`, or None when a locked asset picks the value.

    A Configure inside an asset such as SKD Lookdev references the asset's own
    Output Folder and Last Job, which are set where they live.
    """
    # Every Configure has the folder parms.
    parm = node.parm(name).getReferencedParm()  # ty: ignore[unresolved-attribute]
    held = parm.node()
    if held.isInsideLockedHDA() and not held.isEditableInsideLockedHDA():
        return None
    return parm


def default_root(node: hou.Node) -> str:
    # Lighting renders sit beside the shot, where Nuke's auto-read looks;
    # every other hip keeps its test renders to itself.
    hip = Path(hou.text.expandString("$HIP"))
    base = hip.parent if hip.name == Department.LIGHTING else hip
    return str(base / RENDER_DIRNAME / text(node, LAYER))


def show_next_version(node: hou.Node) -> None:
    if parm := _holder(node, OUTPUT):
        parm.set(str(next_version(Path(text(node, ROOT)))))


def check_saved() -> None:
    if hou.hipFile.isNewFile():
        raise SendRefused(
            "Save the hip file first. Render Root is chosen from where it is saved."
        )


def claim(configures: list[hou.Node]) -> list[Path]:
    """Create every Configure's Output Folder, or none of them."""
    targets = [Path(text(node, OUTPUT)) for node in configures]
    claimed: list[Path] = []
    # Before the refusals below, which show the next version in these parms.
    for node in configures:
        _check_settable(node)
    try:
        for node, folder in zip(configures, targets):
            if _holder(node, OUTPUT) is None:
                raise SendRefused(
                    f"{node.path()} is locked inside its asset, so its Output Folder "
                    "cannot show the next version. Ask the asset's owner to make it "
                    "editable, or to reference its Output Folder from the asset."
                )
            if targets.count(folder) > 1:
                raise SendRefused(
                    f"More than one Submit input renders into {folder}. Wire each "
                    "Configure into Submit once, and give each its own Layer."
                )
        for node, folder in zip(configures, targets):
            _create(node, folder)
            claimed.append(folder)
    except SendRefused:
        # Release before showing the next version, so it can count these again.
        release(claimed)
        for node in configures:
            show_next_version(node)
        raise
    return claimed


def _check_settable(node: hou.Node) -> None:
    """Send sets Output Folder and Last Job, which a lock or a take forbids."""
    take = hou.takes.currentTake()
    for name in (OUTPUT, LAST_JOB):
        parm = _holder(node, name)
        if parm is None:
            continue
        label = f"{parm.description()} on {parm.node().path()}"
        if parm.isLocked():
            raise SendRefused(
                f"{label} is locked, so Send can't show this render in it. "
                "Unlock the parameter, then Send again."
            )
        if take != hou.takes.rootTake() and not take.hasParmTuple(parm.tuple()):
            raise SendRefused(
                f'The take "{take.name()}" leaves out {label}, so Send '
                "can't show this render in it. Switch to the Main take, then "
                "Send again."
            )


def _link_render(root: Path) -> None:
    """Creates a symlink on disk: the `render` folder of a production hip."""
    render = root.parent
    if render.name == RENDER_DIRNAME and render.is_relative_to(get_production_path()):
        link_to_cache(render)


def _create(node: hou.Node, folder: Path) -> None:
    stale = SendRefused(
        f"The Output Folder of {node.path()} was {text(node, OUTPUT) or 'empty'}, "
        "which is not the next free version. It now shows the next one; Send "
        "again to render into it."
    )
    root = Path(text(node, ROOT))
    if folder != next_version(root):
        raise stale
    _link_render(root)
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        if folder.exists():
            raise stale from None
        # mkdir reports a link to a missing folder as a file in its way.
        link = next(p for p in folder.parents if p.is_symlink() and not p.exists())
        raise SendRefused(
            f"Could not create {folder}: {link} links to {link.readlink()}, which "
            "does not exist. Ask a TD to repair the link, then Send again."
        ) from None
    except OSError as error:
        raise SendRefused(f"Could not create {folder}: {error.strerror}.") from None


def release(folders: list[Path]) -> None:
    # Only folders this Send created, so nothing else lives in them.
    for folder in folders:
        shutil.rmtree(folder)


def record_sent(node: hou.Node, folder: Path, job_id: int | None) -> None:
    """Show the Send in Last Job and the next version in Output Folder.

    `job_id` is None when Tractor never confirmed the job.
    """
    if parm := _holder(node, LAST_JOB):
        job = f"job {job_id}" if job_id is not None else "not confirmed by Tractor"
        parm.set(f"{folder}  ({job})")
    show_next_version(node)
