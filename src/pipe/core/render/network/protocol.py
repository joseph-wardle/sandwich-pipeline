"""What the Ranch window and its headless Houdini worker say to each other.

The window runs in mayapy for its Qt binding and has no `hou`; the worker runs
in hython and has no window. The window starts the worker once per action with
a command, and the worker answers by writing one JSON file:

    worker.py describe --shot A_080 --result out.json
    worker.py save     --shot A_080 --result out.json --values values.json
    worker.py send     --shot A_080 --result out.json --values values.json
    worker.py publish-lighting --shot A_080 --result out.json --choice choice.json

Neither side imports the other, so this module imports neither `hou` nor Qt.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pipe.core.cache import RENDER_DIRNAME
from pipe.core.shotgrid.paths import build_shot_path
from pipe.core.util.paths import get_production_path

DESCRIBE = "describe"
SAVE = "save"
SEND = "send"
# Publish the shot's lighting hip as its next lighting version, for its lighter.
PUBLISH_LIGHTING = "publish-lighting"
COMMANDS = (DESCRIBE, SAVE, SEND, PUBLISH_LIGHTING)

# The render department's hip: it reads every department's publish and holds
# the shot's one Render Network. Its folder is the shot's `render/` link.
RENDER_HIP_NAME = "render.hipnc"
LIGHTING_HIP = "lighting/lighting.hipnc"
LIGHTING_PUBLISH = "lighting/publish/lighting.usd"
RENDER_NETWORK_TYPE = "SKD::render_network::1.0"
RENDER_NETWORK_NAME = "render_network"

# A parm's value is one entry per component. A component is a plain value, or
# `{EXPRESSION: "$FSTART", EXPRESSION_LANGUAGE: HSCRIPT}` when an expression
# drives it. The window edits only HScript ones, and shows Python ones read-only.
EXPRESSION = "expr"
EXPRESSION_LANGUAGE = "lang"
HSCRIPT = "hscript"
PYTHON = "python"

# Schema entry kinds and keys, written by `pipe.dcc.houdini.render_network.schema`.
FOLDER = "folder"
MULTIPARM = "multiparm"
PARM = "parm"
# Folder styles. Adjacent tabs folders share one tab bar, as in Houdini.
TABS = "tabs"
COLLAPSIBLE = "collapsible"
SIMPLE = "simple"
# Multiparm children name their instance with this, e.g. `lpename_#`.
INSTANCE_TOKEN = "#"

# Parm types the window draws.
TOGGLE = "toggle"
STRING = "string"
INT = "int"
FLOAT = "float"
MENU = "menu"
LABEL = "label"
BUTTON = "button"
SEPARATOR = "separator"

Values = dict[str, list[Any]]


def shot_folder(shot_code: str) -> Path:
    return get_production_path() / build_shot_path(shot_code)


def render_hip(shot_code: str) -> Path:
    return shot_folder(shot_code) / RENDER_DIRNAME / RENDER_HIP_NAME


@dataclass
class Prim:
    """A prim of the Render Network's input stage, as Houdini's scene graph tree shows it."""

    path: str
    type: str
    # The model kind, such as component or assembly, else "".
    kind: str = ""
    # render, proxy or guide; "" for default or for a prim that can't be drawn.
    purpose: str = ""


@dataclass
class Layer:
    """One of the Render Network's render layers and its last Send."""

    name: str
    # The Render Network parm that turns it on.
    toggle: str
    # Where the next Send renders it.
    next_folder: str
    # What its last Send rendered into, and the job, else "".
    last_job: str


@dataclass
class Sent:
    title: str
    folders: list[str]
    # None when Tractor never confirmed the job, which may still be queued.
    job_id: int | None
    unconfirmed: str = ""


@dataclass
class DepartmentLayer:
    """A row of the render hip's load layers node: one department's publish."""

    row: str
    label: str
    enabled: bool
    # CURRENT, or the version name it is pinned to.
    version: str
    # (token, label) of each version it can read, current first.
    choices: list[tuple[str, str]]


@dataclass
class LightingTarget:
    """`pipe.core.publish.Target` for the shot's next lighting version, less its
    shot, which the window has."""

    name: str
    current: str
    version: int
    file_name: str
    file_code: str
    department: str | None
    final: int | None


@dataclass
class Failure:
    """Why the worker did nothing, worded for the artist, and its traceback."""

    message: str
    details: str = ""


@dataclass
class Result:
    shot: str
    hip: str = ""
    # Whether the render hip was on disk before this run.
    hip_existed: bool = False
    # The lighting hip whose Render Network a new render hip copied, else "".
    seeded_from: str = ""
    schema: list[dict[str, Any]] = field(default_factory=list)
    values: Values = field(default_factory=dict)
    # What the parms driven by an expression evaluate to, such as $FSTART's frame.
    evaluated: Values = field(default_factory=dict)
    # Every prim the Render Network's input holds, parents before children.
    prims: list[Prim] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    # What the artist should know about the hip, such as sets it added.
    notes: list[str] = field(default_factory=list)
    # The department publishes the render hip loads, in its node's order.
    departments: list[DepartmentLayer] = field(default_factory=list)
    sent: Sent | None = None
    # What a lighting publish's result box says, one line each.
    published: list[str] = field(default_factory=list)
    # The next lighting version, for the window's publish dialog, which can't
    # read publishes itself: mayapy has no USD. None when it couldn't be read.
    lighting_target: LightingTarget | None = None
    failure: Failure | None = None

    def write(self, path: Path) -> None:
        # Written beside and renamed, so the window never reads half a file.
        partial = path.with_suffix(path.suffix + ".partial")
        partial.write_text(json.dumps(asdict(self)), encoding="utf-8")
        partial.replace(path)

    @classmethod
    def read(cls, path: Path) -> Result:
        raw = json.loads(path.read_text(encoding="utf-8"))
        sent, failure = raw["sent"], raw["failure"]
        return cls(
            shot=raw["shot"],
            hip=raw["hip"],
            hip_existed=raw["hip_existed"],
            seeded_from=raw["seeded_from"],
            schema=raw["schema"],
            values=raw["values"],
            evaluated=raw["evaluated"],
            prims=[Prim(**prim) for prim in raw["prims"]],
            layers=[Layer(**layer) for layer in raw["layers"]],
            notes=raw["notes"],
            departments=[
                DepartmentLayer(
                    row=d["row"],
                    label=d["label"],
                    enabled=d["enabled"],
                    version=d["version"],
                    choices=[(token, label) for token, label in d["choices"]],
                )
                for d in raw.get("departments", [])
            ],
            sent=Sent(**sent) if sent else None,
            published=raw["published"],
            lighting_target=(
                LightingTarget(**raw["lighting_target"])
                if raw.get("lighting_target")
                else None
            ),
            failure=Failure(**failure) if failure else None,
        )


@dataclass
class DepartmentChoice:
    """Whether the render hip loads a department's publish, and which version:
    CURRENT, or a version name such as "v003" to pin it to."""

    enabled: bool
    version: str


@dataclass
class Edits:
    """What Save and Send set: the Render Network's parms, and the render hip's
    department layers by row, such as "cfx"."""

    values: Values
    departments: dict[str, DepartmentChoice] = field(default_factory=dict)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> Edits:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            values=raw["values"],
            departments={
                row: DepartmentChoice(**choice)
                for row, choice in raw.get("departments", {}).items()
            },
        )


@dataclass
class LightingChoice:
    """What the lighter chose in the publish dialog. No playblast: the worker
    has no viewport."""

    note: str
    announce: bool
    final: bool

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> LightingChoice:
        return cls(**json.loads(path.read_text(encoding="utf-8")))
