"""What the Rancher window and its Houdini session say to each other.

The window runs in mayapy, which has Qt but no `hou`, and keeps the shot's
render hip open in a hython. Each request is one JSON line over a local
socket, and Houdini answers each with one `Reply` line, in order.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

from pipe.core.cache import RENDER_DIRNAME
from pipe.core.shotgrid.paths import build_shot_path
from pipe.core.util.paths import get_production_path

COMMAND = "command"
OPEN = "open"
SET_PARM = "set_parm"
REMOVE_INSTANCE = "remove_instance"
REVERT = "revert"
SET_DEPARTMENT = "set_department"
SAVE = "save"
SEND = "send"
PUBLISH_LIGHTING = "publish_lighting"

RENDER_HIP_NAME = "render.hipnc"
LIGHTING_HIP = "lighting/lighting.hipnc"

# A component an expression drives is {EXPRESSION: "$FSTART", LANGUAGE: "Hscript"}.
EXPRESSION = "expression"
LANGUAGE = "language"

_T = TypeVar("_T")


def shot_folder(shot_code: str) -> Path:
    return get_production_path() / build_shot_path(shot_code)


def render_hip(shot_code: str) -> Path:
    return shot_folder(shot_code) / RENDER_DIRNAME / RENDER_HIP_NAME


def request_line(command: str, args: dict[str, Any]) -> bytes:
    return (json.dumps({COMMAND: command, **args}) + "\n").encode()


@dataclass
class ParmState:
    """A parm tuple as Houdini has it now."""

    value: list[Any]
    evaluated: list[Any]
    disabled: bool
    hidden: bool


@dataclass
class Prim:
    """A prim of the Render Network's input stage."""

    path: str
    type: str
    # The model kind, such as component or assembly, else "".
    kind: str = ""
    # render, proxy or guide; "" for default or for a prim that can't be drawn.
    purpose: str = ""


@dataclass
class Layer:
    """A render layer of the Render Network and its last Send."""

    name: str
    # The Render Network parm that turns it on.
    toggle: str
    next_folder: str
    # The folder and job of its last Send, else "".
    last_job: str


@dataclass
class DepartmentLayer:
    """A row of the render hip's load layers node: one department's publish."""

    row: str
    label: str
    enabled: bool
    # CURRENT, or the version it is pinned to.
    version: str
    # (token, label) of each version it can read, current first.
    choices: list[tuple[str, str]]


@dataclass
class LightingTarget:
    """`pipe.core.publish.Target` for the shot's next lighting version, less its shot."""

    name: str
    current: str
    version: int
    file_name: str
    file_code: str
    department: str | None
    final: int | None


@dataclass
class Sent:
    title: str
    folders: list[str]
    # None when Tractor never confirmed the job, which may still be queued.
    job_id: int | None
    unconfirmed: str = ""


@dataclass
class Failure:
    """Why Houdini did nothing, worded for the artist, and its traceback."""

    message: str
    details: str = ""


@dataclass
class Reply:
    """What a request changed. None is unchanged."""

    hip: str | None = None
    hip_existed: bool = False
    # The lighting hip whose Render Network a new render hip copied, else "".
    seeded_from: str = ""
    notes: list[str] | None = None
    schema: list[dict[str, Any]] | None = None
    parms: dict[str, ParmState] | None = None
    prims: list[Prim] | None = None
    layers: list[Layer] | None = None
    departments: list[DepartmentLayer] | None = None
    # Read in hython because mayapy has no USD; None when it couldn't be.
    lighting_target: LightingTarget | None = None
    sent: Sent | None = None
    published: list[str] | None = None
    failure: Failure | None = None

    def to_line(self) -> str:
        return json.dumps(asdict(self)) + "\n"

    @classmethod
    def from_line(cls, line: bytes) -> Reply:
        raw = json.loads(line)
        return cls(
            hip=raw["hip"],
            hip_existed=raw["hip_existed"],
            seeded_from=raw["seeded_from"],
            notes=raw["notes"],
            schema=raw["schema"],
            parms=_optional(
                raw["parms"], lambda r: {n: ParmState(**s) for n, s in r.items()}
            ),
            prims=_optional(raw["prims"], lambda r: [Prim(**p) for p in r]),
            layers=_optional(raw["layers"], lambda r: [Layer(**la) for la in r]),
            departments=_optional(
                raw["departments"], lambda r: [_department(d) for d in r]
            ),
            lighting_target=_optional(
                raw["lighting_target"], lambda r: LightingTarget(**r)
            ),
            sent=_optional(raw["sent"], lambda r: Sent(**r)),
            published=raw["published"],
            failure=_optional(raw["failure"], lambda r: Failure(**r)),
        )


def _optional(raw: Any, read: Callable[[Any], _T]) -> _T | None:
    return None if raw is None else read(raw)


def _department(raw: dict[str, Any]) -> DepartmentLayer:
    # JSON has no tuples.
    choices = [(token, label) for token, label in raw.pop("choices")]
    return DepartmentLayer(**raw, choices=choices)
