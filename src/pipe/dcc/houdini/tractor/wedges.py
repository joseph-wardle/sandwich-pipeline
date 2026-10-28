"""A wedge run's record, `wedge.json`, and what the Send, the blade and review
each do with it: read wedged values, set them, and point the cache at a wedge's
folder.

    <hip>/geo/.tractor/<node>/v003/
    ├── scene.hipnc         the snapshot every task loads
    ├── wedge.json          the record: what was wedged, over which frames
    ├── job                 the Tractor job's id, there once Tractor confirmed it
    ├── w01/                the first wedge
    │   ├── 01_upstream/    each cache it writes, numbered in cook order, since
    │   ├── 02_sim/         two caches may share a file name
    │   └── flipbook/       its HUD-burned frames, there once they are all written
    └── w02/
"""

from __future__ import annotations

import itertools
import json
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import hou

RECORD = "wedge.json"
JOB = "job"
# Bumped when the record's keys change, so a blade or review running other code
# refuses a record it would misread. Tasks of a job sent before a change, and
# runs on disk, keep the record they were sent with.
SCHEMA = 1
FLIPBOOK = "flipbook"
# The frames' file name before the frame number, as the viewer reads them.
FRAMES = "flipbook"
# The shot camera prim of every published shot, which every flipbook looks through.
SHOT_CAMERA = "/cameras/shotCam"
# What a flipbook draws: the stage at the hip's publish node, which is the shot
# as the department would publish it, or only the sent cache's output.
SHOT = "shot"
CACHE = "cache"
# File Cache 2.0 builds its path from a base folder, unless it is given a file.
CONSTRUCTED = 0
# The most frames a flipbook task draws. Each task loads the scene and its
# textures again, which takes about a minute on a dressed set.
FLIPBOOK_CHUNK = 30

Value = float | int | str


@dataclass(frozen=True)
class Wedge:
    name: str
    # The value of each wedged parm, by its path.
    values: dict[str, Value]


@dataclass(frozen=True)
class Rebuild:
    """A cache a wedge writes into its own folder."""

    path: str
    # A sim cooks each frame from the last, so every cache writes from its own
    # first frame, and none past the run's last, which no flipbook draws.
    start: float
    end: float


@dataclass(frozen=True)
class Run:
    title: str
    snapshot: Path
    hip: Path
    node: str
    start: int
    end: int
    fps: float
    # The hip's publish node, whose stage holds the shot camera.
    publish: str
    draw: str
    # In cook order: the caches loading from disk between a wedged parm and
    # what is drawn, and the node if a parm reaches it.
    rebuilds: list[Rebuild]
    wedges: list[Wedge]

    @property
    def folder(self) -> Path:
        return self.snapshot.parent


def chunks(run: Run) -> list[tuple[int, int]]:
    """The start and end frame of each flipbook task, evenly sized so the last
    task isn't a scene load for a few frames."""
    count = run.end - run.start + 1
    tasks = -(-count // FLIPBOOK_CHUNK)
    size = -(-count // tasks)
    return [
        (start, min(start + size - 1, run.end))
        for start in range(run.start, run.end + 1, size)
    ]


def write_record(run: Run) -> Path:
    path = run.folder / RECORD
    data = {
        "schema": SCHEMA,
        "title": run.title,
        "snapshot": str(run.snapshot),
        "hip": str(run.hip),
        "node": run.node,
        "frames": [run.start, run.end],
        "fps": run.fps,
        "publish": run.publish,
        "draw": run.draw,
        # The key keeps its first name, so runs already on disk still read.
        "caches": [{"path": r.path, "frames": [r.start, r.end]} for r in run.rebuilds],
        "wedges": [{"name": w.name, "values": w.values} for w in run.wedges],
    }
    path.write_text(json.dumps(data, indent=2))
    return path


def read_record(path: Path) -> Run:
    """Raises ValueError, worded for the artist, for a record it can't read."""
    data = json.loads(path.read_text())
    if data.get("schema") != SCHEMA:
        raise ValueError("it was sent by another version of Wedge on Tractor")
    start, end = data["frames"]
    return Run(
        title=data["title"],
        snapshot=Path(data["snapshot"]),
        hip=Path(data["hip"]),
        node=data["node"],
        start=start,
        end=end,
        fps=data["fps"],
        publish=data["publish"],
        draw=data["draw"],
        rebuilds=[Rebuild(r["path"], *r["frames"]) for r in data["caches"]],
        wedges=[Wedge(w["name"], w["values"]) for w in data["wedges"]],
    )


def parse(parm: hou.Parm, text: str) -> list[Value]:
    """The values typed for `parm`, separated by spaces. Raises ValueError,
    worded for the artist."""
    tokens = shlex.split(text)
    if not tokens:
        raise ValueError(f"Type the values to wedge {label(parm.path())} with.")
    return [_value(parm, token) for token in tokens]


def _value(parm: hou.Parm, token: str) -> Value:
    template = parm.parmTemplate()
    kind = template.type()
    menu = _menu(parm)
    try:
        if token in menu:
            if kind != hou.parmTemplateType.Int:
                # Kept as the token, which the burn-in shows and set() takes.
                return token
            # An int parm holds the item's index, unless its menu's tokens are
            # the values. The base ParmTemplate doesn't declare this method.
            use_token = template.menuUseToken()  # ty: ignore[unresolved-attribute]
            return int(token) if use_token else menu.index(token)
        if kind == hou.parmTemplateType.Float:
            return float(token)
        if kind in (hou.parmTemplateType.Int, hou.parmTemplateType.Toggle):
            return int(token)
        if kind == hou.parmTemplateType.String:
            return token
    except ValueError:
        pass
    expected = f"one of {', '.join(menu)}" if menu else _EXPECTED.get(kind, "")
    if not expected:
        raise ValueError(f"{label(parm.path())} is not a parm that can be wedged.")
    raise ValueError(f"{token} is not a value of {label(parm.path())}: use {expected}.")


def _menu(parm: hou.Parm) -> tuple[str, ...]:
    """The tokens of the parm's menu, if its type can have one."""
    template = parm.parmTemplate()
    if template.type() not in _MENUS:
        return ()
    # The base ParmTemplate doesn't declare what these subtypes have.
    return tuple(template.menuItems())  # ty: ignore[unresolved-attribute]


_MENUS = (
    hou.parmTemplateType.Int,
    hou.parmTemplateType.Menu,
    hou.parmTemplateType.String,
)
_EXPECTED = {
    hou.parmTemplateType.Float: "numbers",
    hou.parmTemplateType.Int: "whole numbers",
    hou.parmTemplateType.Toggle: "0 or 1",
}


def combine(rows: list[tuple[str, list[Value]]]) -> list[Wedge]:
    """One wedge for each combination of the rows' values."""
    paths = [path for path, _ in rows]
    combos = itertools.product(*(values for _, values in rows))
    return [
        Wedge(f"w{index:02}", dict(zip(paths, combo, strict=True)))
        for index, combo in enumerate(combos, start=1)
    ]


def set_values(wedge: Wedge) -> list[str]:
    """Set the wedge's values in the scene. The paths of parms it no longer has."""
    missing = []
    for path, value in wedge.values.items():
        parm = hou.parm(path)
        if parm is None:
            missing.append(path)
            continue
        # A keyframe or an expression would override the value.
        parm.deleteAllKeyframes()
        parm.set(value)
    return missing


def redirect(node: hou.Node, folder: Path, index: int) -> None:
    """Point the File Cache's output into its numbered folder in the wedge's
    `folder`, keeping its own file names."""
    folder = folder / f"{index:02}_{node.name()}"
    if node.evalParm("filemethod") == CONSTRUCTED:
        node.setParms({"basedir": str(folder)})
        return
    # Unexpanded, so the name keeps its $OS and $F. node.parm may return None,
    # but every File Cache has a file parm.
    file = node.parm("file").unexpandedString()  # ty: ignore[unresolved-attribute]
    node.setParms({"file": str(folder / PurePosixPath(file).name)})


def label(path: str) -> str:
    """A parm by its node's name, which is how the artist knows it."""
    node, _, parm = path.rpartition("/")
    return f"{node.rpartition('/')[2]}/{parm}"


def describe(wedge: Wedge) -> list[str]:
    return [f"{label(path)} {_text(v)}" for path, v in wedge.values.items()]


def _text(value: Value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)
