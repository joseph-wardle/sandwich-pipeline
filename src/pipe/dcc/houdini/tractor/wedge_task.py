"""What a Wedge Send task runs on a blade: hython sims one wedge of a run,
flipbooks some of its frames, finishes its flipbook once every frame is drawn,
or prepares and burns its render.

    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> sim <cache>
    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> flipbook <start> <end>
    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> finish
    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> prepare
    hython -m pipe.dcc.houdini.tractor.wedge_task <wedge.json> <wedge> burn

Sim, flipbook and prepare set the wedge's values and point every cache the
wedge writes into its own folder in the wedge's. A sim writes one of them over
the frames the record gives it, reading those written before it, and the
flipbook reads them all, drawing the scene as the sims saw it. It looks through
the shot camera at the stage of the hip's publish node, or at only the wedged
cache's output under that camera and the shot's lights.

Prepare writes what the flipbook draws as a shot layer's render.usd, with the
show's RenderMan settings at daily quality, for the layer's Render and Denoise
tasks. Burn turns the denoised frames into HUD-burned frames in the show's view.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import hou
from pxr import Sdf, Usd

from pipe.core import color
from pipe.core.hud import HudContent
from pipe.core.playblast.encoding import burn_hud_frames
from pipe.core.render import RENDER_USD
from pipe.dcc.houdini.tractor import (
    SendRefused,
    cache_task,
    parms,
    render_send,
    snapshot,
    wedges,
)
from pipe.dcc.houdini.tractor.caches import LOAD_FROM_DISK
from pipe.dcc.houdini.tractor.commands import (
    WEDGE_BURN,
    WEDGE_DRAW_FAILED,
    WEDGE_FINISH,
    WEDGE_FLIPBOOK,
    WEDGE_PREPARE,
    WEDGE_SIM,
)
from pipe.dcc.houdini.util.nodetypes import (
    RENDERMAN_SETTINGS,
    TRACTOR_CONFIGURE,
    TRACTOR_DENOISE,
)

# The Flipbook ROP's Source menu.
LOP_SOURCE = 1
# Its Lighting menu's Work Lights, a headlight by default. The viewport draws
# the shot's lights, set for RenderMan, far too bright.
WORK_LIGHTS = 1
RAW = "raw"
# The lights of every published shot. Cache only keeps them, so a render of the
# cache alone is lit as the shot is.
LIGHTS = "/lights"
# The set that hides a shot's dome from the camera isn't drawn in Cache only,
# so the dome's picture would fill the frame behind the cache.
CAMERA_VISIBILITY = "primvars:ri:attributes:visibility:camera"
DOME_LIGHTS = ("DomeLight", "DomeLight_1")
# Daily quality: the most samples a pixel takes. Denoise cleans up the rest.
DAILY_SAMPLES = 64
MAX_SAMPLES = "xn__rihidermaxsamples_n3af"
MAX_SAMPLES_CONTROL = "xn__rihidermaxsamples_control_ohbf"
STEPS = {
    WEDGE_SIM: 1,
    WEDGE_FLIPBOOK: 2,
    WEDGE_FINISH: 0,
    WEDGE_PREPARE: 0,
    WEDGE_BURN: 0,
}


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    run = wedges.read_record(args.record)
    wedge = next(w for w in run.wedges if w.name == args.wedge)
    folder = run.folder / wedge.name
    if args.step == WEDGE_FINISH:
        return finish(run, folder)
    if args.step == WEDGE_BURN:
        return burn(run, wedge, folder)
    snapshot.load(run.snapshot, run.hip)
    missing = wedges.set_values(wedge)
    if missing:
        print(f"The snapshot has no parm {', '.join(missing)}.", file=sys.stderr)
        return 1
    rebuilt: list[hou.Node] = []
    for index, path in enumerate((r.path for r in run.rebuilds), start=1):
        node = hou.node(path)
        if node is None:
            print(f"The snapshot has no node {path}.", file=sys.stderr)
            return 1
        wedges.redirect(node, folder, index)
        rebuilt.append(node)
    if args.step == WEDGE_SIM:
        return sim(run, rebuilt, args.args[0])
    # The flipbook reads what the sims wrote, as the artist's viewport would.
    for node in rebuilt:
        node.setParms({LOAD_FROM_DISK: 1})
    if args.step == WEDGE_PREPARE:
        return prepare(run, wedge)
    start, end = (int(frame) for frame in args.args)
    return flipbook(run, wedge, folder, start, end)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="the run's wedge.json")
    parser.add_argument("wedge", help="the wedge's name in the record")
    parser.add_argument("step", choices=tuple(STEPS))
    parser.add_argument(
        "args", nargs="*", help="the cache a sim writes, or a flipbook's start and end"
    )
    args = parser.parse_args(argv)
    wanted = STEPS[args.step]
    if len(args.args) != wanted:
        parser.error(f"{args.step} takes {wanted} arguments, not {len(args.args)}")
    return args


def sim(run: wedges.Run, rebuilt: list[hou.Node], path: str) -> int:
    """Write one of the wedge's caches over the frames the record gives it,
    reading those written before it from the wedge's folder. The exit code."""
    index = [r.path for r in run.rebuilds].index(path)
    for node in rebuilt[:index]:
        node.setParms({LOAD_FROM_DISK: 1})
    rebuild = run.rebuilds[index]
    return cache_task.write(path, rebuild.start, rebuild.end)


def flipbook(
    run: wedges.Run, wedge: wedges.Wedge, folder: Path, start: int, end: int
) -> int:
    """Draw `start` to `end` through the shot camera and burn the wedge's values on.

    The frames are written aside, for `finish` to rename into place, so review
    never lists a wedge whose flipbook is half written.
    """
    scratch = folder / wedges.FLIPBOOK_TMP
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
        str(scratch / f"{wedges.FRAME_BASENAME}.%04d.png"),
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
    scratch = folder / wedges.FLIPBOOK_TMP
    final = folder / wedges.FLIPBOOK
    frames = range(run.start, run.end + 1)
    # Finished already, and left alone, since review may be playing it.
    if not scratch.exists() and _missing(frames, final) is None:
        print(f"{final} already has all {len(frames)} frames.")
        return 0
    # Checked before moving anything, so a failure leaves review its frames.
    missing = _missing(frames, scratch, final)
    if missing is not None:
        print(f"The flipbook has no frame {missing}.", file=sys.stderr)
        return 1
    if final.is_dir():
        scratch.mkdir(exist_ok=True)
        for file in final.iterdir():
            if not (scratch / file.name).exists():
                file.rename(scratch / file.name)
    shutil.rmtree(final, ignore_errors=True)
    scratch.rename(final)
    print(f"Flipbooked {len(frames)} frames into {final}.")
    return 0


def _missing(frames: range, *folders: Path) -> int | None:
    """The first of `frames` none of the folders has a burned frame of."""
    for frame in frames:
        name = f"{wedges.FRAME_BASENAME}.{frame:04}.png"
        if not any((folder / name).is_file() for folder in folders):
            return frame
    return None


def _flipbook_rop(run: wedges.Run) -> hou.RopNode | None:
    """A Flipbook ROP that draws what the run chose, or None after saying why it
    can't."""
    drawn = _drawn(run)
    if drawn is None:
        return None
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


def _drawn(run: wedges.Run) -> hou.LopNode | None:
    """What the run chose to draw, or None after saying why it can't be drawn."""
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
    return drawn


def _cache_only(publish: hou.LopNode, node: str) -> hou.LopNode:
    """A stage of the cache's output alone, under the publish node's shot camera
    and lights.

    The publish node has no output, so the camera is kept from its input.
    """
    stage = publish.parent()
    camera = stage.createNode("prune")
    camera.setInput(0, publish.input(0))
    camera.setParms(
        {
            "primpattern1": f"{wedges.SHOT_CAMERA} {LIGHTS} {LIGHTS}/**",
            "pruneunselected": 1,
            "method": "deactivate",
        }
    )
    # A Merge that flattens its inputs drops what sits above a Layer Break, as
    # the camera does in an fx hip, so a SOP is wired after the Prune, and a LOP
    # cache's layers are kept apart. A Fetch reaches a cache in another network.
    if isinstance(hou.node(node), hou.LopNode):
        fetch = stage.createNode("fetch")
        fetch.setParms({"loppath": node})
        merge = stage.createNode("merge")
        merge.setInput(0, camera)
        merge.setInput(1, fetch)
        merge.setParms({"mergestyle": "separatelayers"})
        # createNode is typed as returning any node; this one is a LOP.
        return merge  # ty: ignore[invalid-return-type]
    # SOP Import draws the geometry where the SOP has it, without any transform
    # of the object around it. Sims are made in world space.
    geometry = stage.createNode("sopimport")
    geometry.setInput(0, camera)
    geometry.setParms({"soppath": node})
    # createNode is typed as returning any node; this one is a LOP.
    return geometry  # ty: ignore[invalid-return-type]


def prepare(run: wedges.Run, wedge: wedges.Wedge) -> int:
    """Write the wedge's render.usd and denoise.json as a shot layer's, with
    the show's RenderMan settings at daily quality. The exit code."""
    drawn = _drawn(run)
    if drawn is None:
        return 1
    # Only a run that renders has Prepare tasks.
    assert run.render is not None
    expected = wedges.render_layer(run, wedge.name)
    network = drawn.parent()
    # The publish node has no output to wire from.
    fetch = network.createNode("fetch")
    fetch.setParms({"loppath": drawn.path()})
    settings = network.createNode(RENDERMAN_SETTINGS)
    settings.setInput(0, fetch)
    settings.setParms({MAX_SAMPLES_CONTROL: "set", MAX_SAMPLES: DAILY_SAMPLES})
    # createNode is typed as returning any node; this one is a LOP.
    configure: hou.LopNode
    configure = network.createNode(TRACTOR_CONFIGURE)  # ty: ignore[invalid-assignment]
    configure.setInput(0, settings)
    width, height = run.render
    _set(
        configure,
        {
            parms.LAYER: wedge.name,
            parms.OUTPUT: str(expected.folder),
            "trange": "normal",
            "f1": run.start,
            "f2": run.end,
            "f3": 1,
            parms.RESOLUTION: "specific",
            "res_user1": width,
            "res_user2": height,
        },
    )
    denoise = network.createNode(TRACTOR_DENOISE)
    denoise.setInput(0, configure)
    # A retry after Render keeps its frames, which Denoise and Burn still read.
    expected.folder.mkdir(parents=True, exist_ok=True)
    chain = render_send.Chain(configure, denoise, None, denoise)
    try:
        # The snapshot's publishes are already the versions the artist saw.
        written = render_send.layer(chain, lambda _current: None)
    except SendRefused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    # The job's Render and Denoise tasks were built from `expected` at Send.
    if written != expected:
        print(f"render.usd renders {written}, not {expected}.", file=sys.stderr)
        return 1
    if run.draw == wedges.CACHE:
        _hide_domes(expected.folder / RENDER_USD)
    print(f"Wrote {expected.folder / RENDER_USD}.")
    return 0


def _set(node: hou.Node, values: dict[str, str | int | float]) -> None:
    """Set parms whose defaults are expressions, which setParms alone would keep."""
    for name in values:
        node.parm(name).deleteAllKeyframes()  # ty: ignore[unresolved-attribute]
    node.setParms(values)


def _hide_domes(path: Path) -> None:
    stage = Usd.Stage.Open(str(path))
    for prim in stage.Traverse():
        if any(prim.IsA(name) for name in DOME_LIGHTS):
            prim.CreateAttribute(CAMERA_VISIBILITY, Sdf.ValueTypeNames.Int).Set(0)
    stage.GetRootLayer().Save()


def burn(run: wedges.Run, wedge: wedges.Wedge, folder: Path) -> int:
    """Turn the denoised frames into the show's view, burn the wedge's values
    on, and move them into place. The exit code.

    Each frame's EXR holds every pass Denoise read, so the render's folder is
    deleted once the frames are burned.
    """
    # Only a run that renders has Burn tasks.
    assert run.render is not None
    layer = wedges.render_layer(run, wedge.name)
    final = folder / wedges.RENDER
    frames = range(run.start, run.end + 1)
    # Burned already, and left alone, since review may be playing it.
    if not layer.folder.exists() and _missing(frames, final) is None:
        print(f"{final} already has all {len(frames)} frames.")
        return 0
    scratch = layer.folder / wedges.BURN_TMP
    raw = scratch / RAW
    shutil.rmtree(scratch, ignore_errors=True)
    raw.mkdir(parents=True)
    # A static method the stubs declare as an instance one.
    config = hou.Color.ocio_configPath()  # ty: ignore[missing-argument]
    for frame in frames:
        image = layer.folder / wedges.PRODUCT / f"{frame:04}.exr"
        png = raw / f"{RAW}.{frame:04}.png"
        converted = subprocess.run(
            ["hoiiotool", "--colorconfig", config, str(image), "--ch", "R,G,B"]
            + ["--ociodisplay:from=scene_linear", color.DISPLAY, color.DEFAULT_VIEW]
            + ["-d", "uint8", "-o", str(png)]
        )
        if converted.returncode != 0:
            print(f"hoiiotool couldn't convert {image}.", file=sys.stderr)
            return 1
    burn_hud_frames(
        str(raw / f"{RAW}.%04d.png"),
        str(scratch / f"{wedges.FRAME_BASENAME}.%04d.png"),
        _hud(run, wedge, run.start),
        run.render,
        start_frame=run.start,
    )
    shutil.rmtree(raw)
    missing = _missing(frames, scratch)
    if missing is not None:
        print(f"The render has no burned frame {missing}.", file=sys.stderr)
        return 1
    shutil.rmtree(final, ignore_errors=True)
    scratch.rename(final)
    shutil.rmtree(layer.folder)
    print(f"Burned {len(frames)} rendered frames into {final}.")
    return 0


def _hud(run: wedges.Run, wedge: wedges.Wedge, start: int) -> HudContent:
    # Rows stack bottom-up, so the values read top-down in the record's order.
    values = tuple(reversed(wedges.describe(wedge)))
    name = f"{run.title} {run.folder.name} {wedge.name}"
    return HudContent(left_lines=(*values, name), frame_start=start)


if __name__ == "__main__":
    sys.exit(main())
