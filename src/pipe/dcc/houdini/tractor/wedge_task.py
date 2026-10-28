"""What a Wedge Send task runs on a blade: hython sims one wedge of a run,
flipbooks some of its frames, or finishes its flipbook once every frame is drawn.

    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> sim <cache>
    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> flipbook <start> <end>
    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> finish

Sim and flipbook set the wedge's values and point every cache the wedge writes
into its own folder in the wedge's. A sim writes one of them over the frames the
record gives it, reading those written before it, and the flipbook reads them
all, drawing the scene as the sims saw it. It looks through the shot camera at
the stage of the hip's publish node, or at only the wedged cache's output under
that camera.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import hou

from pipe.core.hud import HudContent
from pipe.core.playblast.encoding import burn_hud_frames
from pipe.dcc.houdini.tractor import cache_task, snapshot, wedges
from pipe.dcc.houdini.tractor.caches import LOAD_FROM_DISK
from pipe.dcc.houdini.tractor.commands import (
    WEDGE_DRAW_FAILED,
    WEDGE_FINISH,
    WEDGE_FLIPBOOK,
    WEDGE_SIM,
)

# The Flipbook ROP's Source menu.
LOP_SOURCE = 1
# Its Lighting menu's Work Lights, a headlight by default. The viewport draws
# the shot's lights, set for RenderMan, far too bright.
WORK_LIGHTS = 1
RAW = "raw"
SCRATCH = f"{wedges.FLIPBOOK}.tmp"


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    run = wedges.read_record(args.record)
    wedge = next(w for w in run.wedges if w.name == args.wedge)
    folder = run.folder / wedge.name
    if args.step == WEDGE_FINISH:
        return finish(run, folder)
    snapshot.load(run.snapshot, run.hip)
    missing = wedges.set_values(wedge)
    if missing:
        print(f"The snapshot has no parm {', '.join(missing)}.", file=sys.stderr)
        return 1
    written: list[hou.Node] = []
    for index, path in enumerate((c.path for c in run.caches), start=1):
        node = hou.node(path)
        if node is None:
            print(f"The snapshot has no node {path}.", file=sys.stderr)
            return 1
        wedges.redirect(node, folder, index)
        written.append(node)
    if args.step == WEDGE_SIM:
        return sim(run, written, args.args[0])
    # The flipbook reads what the sims wrote, as the artist's viewport would.
    for node in written:
        node.setParms({LOAD_FROM_DISK: 1})
    start, end = (int(frame) for frame in args.args)
    return flipbook(run, wedge, folder, start, end)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="the run's wedge.json")
    parser.add_argument("wedge", help="the wedge's name in the record")
    parser.add_argument("step", choices=(WEDGE_SIM, WEDGE_FLIPBOOK, WEDGE_FINISH))
    parser.add_argument(
        "args", nargs="*", help="the cache a sim writes, or a flipbook's start and end"
    )
    args = parser.parse_args(argv)
    wanted = {WEDGE_SIM: 1, WEDGE_FLIPBOOK: 2, WEDGE_FINISH: 0}[args.step]
    if len(args.args) != wanted:
        parser.error(f"{args.step} takes {wanted} arguments, not {len(args.args)}")
    return args


def sim(run: wedges.Run, written: list[hou.Node], path: str) -> int:
    """Write one of the wedge's caches over the frames the record gives it,
    reading those written before it from the wedge's folder. The exit code."""
    paths = [c.path for c in run.caches]
    if path not in paths:
        print(f"The run doesn't write {path}.", file=sys.stderr)
        return 1
    index = paths.index(path)
    for node in written[:index]:
        node.setParms({LOAD_FROM_DISK: 1})
    return cache_task.write(path, run.caches[index].start, run.caches[index].end)


def flipbook(
    run: wedges.Run, wedge: wedges.Wedge, folder: Path, start: int, end: int
) -> int:
    """Draw `start` to `end` through the shot camera and burn the wedge's values on.

    The frames are written aside, for `finish` to rename into place, so review
    never lists a wedge whose flipbook is half written.
    """
    scratch = folder / SCRATCH
    # Its own folder, since ffmpeg would also burn the frames of other tasks.
    raw = scratch / f"{RAW}.{start}-{end}"
    shutil.rmtree(raw, ignore_errors=True)
    raw.mkdir(parents=True)
    rop = _flipbook_rop(run)
    if rop is None:
        return 1
    rop.setParms({"picture": str(raw / f"{RAW}.$F4.png")})
    try:
        rop.render(frame_range=(start, end), output_progress=True)
    except hou.OperationFailed as error:
        print(f"The flipbook couldn't draw: {error}", file=sys.stderr)
        return WEDGE_DRAW_FAILED

    frames = range(start, end + 1)
    missing = [f for f in frames if not (raw / f"{RAW}.{f:04}.png").is_file()]
    if missing:
        print(f"The flipbook wrote no frame {missing[0]}.", file=sys.stderr)
        return 1
    width, height = hou.imageResolution(str(raw / f"{RAW}.{start:04}.png"))
    burn_hud_frames(
        str(raw / f"{RAW}.%04d.png"),
        str(scratch / f"{wedges.FRAMES}.%04d.png"),
        _hud(run, wedge, start),
        (width, height),
        start_frame=start,
    )
    shutil.rmtree(raw)
    print(f"Flipbooked frames {start}-{end} of {wedge.name}.")
    return 0


def finish(run: wedges.Run, folder: Path) -> int:
    """Rename the drawn frames into place once every one is there.

    A retried Draw writes into scratch again, after the flipbook is in place,
    so finishing again keeps the flipbook's frames that Draw didn't redraw.
    """
    scratch = folder / SCRATCH
    final = folder / wedges.FLIPBOOK
    frames = range(run.start, run.end + 1)
    # Finished already, and left alone, since review may be playing it.
    if not scratch.exists() and _missing(final, frames) is None:
        print(f"{final} already has all {len(frames)} frames.")
        return 0
    if final.is_dir():
        scratch.mkdir(exist_ok=True)
        for file in final.iterdir():
            if not (scratch / file.name).exists():
                file.rename(scratch / file.name)
    missing = _missing(scratch, frames)
    if missing is not None:
        print(f"The flipbook has no frame {missing}.", file=sys.stderr)
        return 1
    shutil.rmtree(final, ignore_errors=True)
    scratch.rename(final)
    print(f"Flipbooked {len(frames)} frames into {final}.")
    return 0


def _missing(folder: Path, frames: range) -> int | None:
    """The first of `frames` the folder has no burned frame of."""
    for frame in frames:
        if not (folder / f"{wedges.FRAMES}.{frame:04}.png").is_file():
            return frame
    return None


def _flipbook_rop(run: wedges.Run) -> hou.RopNode | None:
    """A Flipbook ROP that draws what the run chose, or None after saying why it
    can't."""
    publish = hou.node(run.publish)
    if not isinstance(publish, hou.LopNode):
        print(f"The snapshot has no publish node {run.publish}.", file=sys.stderr)
        return None
    drawn = publish if run.draw == wedges.SHOT else _cache_only(publish, run.node)
    stage = drawn.stage()
    if stage is None:
        print(
            f"The stage at {drawn.path()} doesn't cook.",
            *drawn.errors(),
            sep="\n",
            file=sys.stderr,
        )
        return None
    # A missing camera prim draws a blank frame without an error.
    if not stage.GetPrimAtPath(wedges.SHOT_CAMERA):
        print(
            f"The stage of {run.publish} has no camera {wedges.SHOT_CAMERA}.",
            file=sys.stderr,
        )
        return None
    # The viewport's renderer. The OpenGL ROP reloads every texture each frame,
    # which made a textured set minutes per frame.
    # hou.node may return None, but /out is always there.
    rop = hou.node("/out").createNode("flipbook")  # ty: ignore[unresolved-attribute]
    rop.setParms(
        {
            "opsource": LOP_SOURCE,
            "loppath": drawn.path(),
            "cameraprim": wedges.SHOT_CAMERA,
            "lighting": WORK_LIGHTS,
            "trange": 1,
            "f3": 1,
        }
    )
    # createNode is typed as returning any node; this one is a ROP.
    return rop  # ty: ignore[invalid-return-type]


def _cache_only(publish: hou.LopNode, node: str) -> hou.LopNode:
    """A stage of the cache's output alone, under the publish node's shot camera.

    The publish node has no output, so the camera is kept from its input.
    """
    stage = publish.parent()
    camera = stage.createNode("prune")
    camera.setInput(0, publish.input(0))
    camera.setParms(
        {
            "primpattern1": wedges.SHOT_CAMERA,
            "pruneunselected": 1,
            "method": "deactivate",
        }
    )
    # SOP Import draws the geometry where the SOP has it, without any transform
    # of the object around it. Sims are made in world space. It is wired after
    # the Prune, not merged with it: a Merge flattens its inputs, which drops
    # what sits above a Layer Break, as the camera does in an fx hip.
    geometry = stage.createNode("sopimport")
    geometry.setInput(0, camera)
    geometry.setParms({"soppath": node})
    # createNode is typed as returning any node; this one is a LOP.
    return geometry  # ty: ignore[invalid-return-type]


def _hud(run: wedges.Run, wedge: wedges.Wedge, start: int) -> HudContent:
    # Rows stack bottom-up, so the values read top-down in the record's order.
    values = tuple(reversed(wedges.describe(wedge)))
    name = f"{run.title} {run.folder.name} {wedge.name}"
    return HudContent(left_lines=(*values, name), frame_start=start)


if __name__ == "__main__":
    sys.exit(main())
