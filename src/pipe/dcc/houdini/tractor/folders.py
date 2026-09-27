"""Version folders: each Send renders into a `v###` folder no other Send has used.

Configure's Output Folder shows the exact folder the next Send claims. Send
claims it with an exclusive `mkdir`, and when that folder is taken or no longer
the next free version it refuses and shows the next one rather than silently
taking another.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import hou

from pipe.dcc.houdini.hipfile.departments import Department
from pipe.dcc.houdini.tractor import SendRefused

LAYER = "layer"
ROOT = "render_root"
# Named for the USD ROP parm inside Configure that reads it.
OUTPUT = "savetodirectory_directory"
LAST_JOB = "last_job"

_VERSION = re.compile(r"v(\d+)")


def _text(node: hou.Node, name: str) -> str:
    # Every folder parm is a string parm; evalParm's type covers all kinds.
    return str(node.evalParm(name))


def _owned(node: hou.Node) -> bool:
    # An enclosing asset such as SKD Lookdev picks the folder and shows it itself.
    return not node.isInsideLockedHDA()


def default_root(node: hou.Node) -> str:
    # Lighting renders sit beside the shot, where Nuke's auto-read looks;
    # every other hip keeps its test renders to itself.
    hip = Path(hou.text.expandString("$HIP"))
    base = hip.parent if hip.name == Department.LIGHTING else hip
    return str(base / "render" / _text(node, LAYER))


def next_version(root: Path) -> Path:
    taken = [int(m[1]) for p in root.glob("v*") if (m := _VERSION.fullmatch(p.name))]
    return root / f"v{max(taken, default=0) + 1:03}"


def show_next_version(node: hou.Node) -> None:
    node.setParms({OUTPUT: str(next_version(Path(_text(node, ROOT))))})


def check_saved() -> None:
    if hou.hipFile.isNewFile():
        raise SendRefused(
            "Save the hip file first. Render Root is chosen from where it is saved."
        )


def claim(configures: list[hou.Node]) -> list[Path]:
    """Create every Configure's Output Folder, or none of them."""
    targets = [Path(_text(node, OUTPUT)) for node in configures]
    claimed: list[Path] = []
    try:
        for folder in targets:
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
            if _owned(node):
                show_next_version(node)
        raise
    return claimed


def _create(node: hou.Node, folder: Path) -> None:
    # An enclosing asset picks its folder again on every Send.
    shown = " Output Folder now shows the next one." if _owned(node) else ""
    stale = SendRefused(
        f"{node.path()} was about to render into {folder}, which is not the next "
        f"free version.{shown} Send again to render into the next one."
    )
    if _owned(node) and folder != next_version(Path(_text(node, ROOT))):
        raise stale
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        raise stale from None
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
    if not _owned(node):
        return
    job = f"job {job_id}" if job_id is not None else "not confirmed by Tractor"
    node.setParms({LAST_JOB: f"{folder}  ({job})"})
    show_next_version(node)
