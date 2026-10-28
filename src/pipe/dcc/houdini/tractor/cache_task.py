"""What a Cache Send task runs on a blade: hython writes one File Cache's frames
from the Send's snapshot of the hip.

    hython -m pipe.dcc.houdini.tractor.cache_task <snapshot> <hip> <node> <start> <end>

The File Cache reads a missing or truncated frame as empty geometry with only a
warning, and a frame the ROP skips keeps an earlier save's file. So the task
touches a marker before writing, and passes a frame only if it is newer than the
marker and loads back. Any other frame exits non-zero, which fails the task in
Tractor. The earlier frames stay until they are written over, so the artist's
scene still loads them while the task runs, and after it fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import hou
from pxr import Sdf

from pipe.dcc.houdini.tractor import snapshot
from pipe.dcc.houdini.util.nodetypes import FILE_CACHES

# The ROP inside every File Cache that its Save to Disk button presses.
ROP = "render"
# The file parm of that ROP, by its network type.
OUTPUTS = {"Sop": "sopoutput", "Lop": "lopoutput"}
# A step is a float; this keeps the last frame in when float steps fall just short of it.
STEP_TOLERANCE = 1e-6
# Touched before the ROP writes; a frame older than it is an earlier save's.
MARKER = ".tractor_writing"


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    snapshot.load(args.snapshot, args.hip)
    return write(args.node, args.start, args.end)


def write(path: str, start: float, end: float) -> int:
    """Write the File Cache at `path` from `start` to `end`. The exit code."""
    rop = _rop(path)
    # A LOP cache writes every frame into one file.
    files = list(dict.fromkeys(_output(rop, f) for f in _frames(rop, start, end)))
    # Beside the frames, so the file server stamps both and blade clocks don't
    # matter. Named by the range, since a cache's chunks write the same folder.
    marker = files[0].parent / f"{MARKER}.{start:g}-{end:g}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    # Created anew, since touching an existing file may keep its time on NFS.
    marker.unlink(missing_ok=True)
    marker.touch()
    started = marker.stat().st_mtime_ns
    try:
        if rop.evalParm("trange"):
            rop.render(frame_range=(start, end), output_progress=True)
        else:
            rop.render(output_progress=True)
    finally:
        marker.unlink(missing_ok=True)
    unreadable = [file for file in files if not _fresh(file, started)]
    for file in unreadable:
        print(f"Not written by this task, or not readable: {file}", file=sys.stderr)
    if unreadable:
        print(
            f"{len(unreadable)} of {len(files)} frames of {path} failed.",
            file=sys.stderr,
        )
        return 1
    print(f"Wrote {len(files)} frames of {path}.")
    return 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="the hip Send wrote for this job")
    parser.add_argument("hip", type=Path, help="the artist's hip the snapshot is of")
    parser.add_argument("node", help="the File Cache to write")
    parser.add_argument("start", type=float)
    parser.add_argument("end", type=float)
    return parser.parse_args(argv)


def _rop(path: str) -> hou.RopNode:
    node = hou.node(path)
    if node is None or node.type().nameWithCategory() not in FILE_CACHES:
        raise SystemExit(f"{path} is not a File Cache node in the snapshot.")
    # Every File Cache holds this ROP; hou types the child as any node.
    return node.node(ROP)  # ty: ignore[invalid-return-type]


def _parm(rop: hou.RopNode, name: str) -> hou.Parm:
    # The stock ROP always has the parms this module reads.
    return rop.parm(name)  # ty: ignore[invalid-return-type]


def _frames(rop: hou.RopNode, start: float, end: float) -> list[float]:
    """Every time the ROP writes, substeps included."""
    if not rop.evalParm("trange"):
        return [hou.frame()]
    # The ROP's own increment: the node's step divided by its substeps.
    step = _parm(rop, "f3").evalAsFloat()
    count = int((end - start) / step + STEP_TOLERANCE)
    return [start + i * step for i in range(count + 1)]


def _output(rop: hou.RopNode, frame: float) -> Path:
    name = OUTPUTS[rop.type().category().name()]
    return Path(_parm(rop, name).evalAsStringAtFrame(frame))


def _fresh(file: Path, started: int) -> bool:
    """Whether this task wrote `file` and it loads. An earlier save's frame is
    seconds older than the marker at least, so a tie is a frame just written."""
    if not file.is_file() or file.stat().st_mtime_ns < started:
        return False
    if file.suffix.startswith(".usd"):
        # None for a file USD can't read.
        return Sdf.Layer.OpenAsAnonymous(str(file)) is not None
    try:
        hou.Geometry().loadFromFile(str(file))
    except hou.OperationFailed:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
