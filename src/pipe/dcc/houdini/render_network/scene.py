"""A shot's render hip, open in the Sandwich Renderer's headless Houdini.

The render hip reads every department's publish, lighting's among them, and
holds the shot's one Render Network. Its Render Network renders what lighting
last published, never a lighter's unpublished edits.

    shot/<code>/render/render.hipnc
        sets → load layers → layer break → BEGIN_RENDER → render_network → END_RENDER
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import hou
from pxr import Usd, UsdGeom

from pipe.core.cache import RENDER_DIRNAME, link_to_cache
from pipe.core.render.protocol import (
    LIGHTING_HIP,
    DepartmentLayer,
    Layer,
    Prim,
    shot_folder,
)
from pipe.core.sets import current_layer_path
from pipe.core.shotgrid import Shot
from pipe.core.util.paths import get_production_path
from pipe.dcc.houdini.hipfile.departments import Department
from pipe.dcc.houdini.hipfile.shot import build_department_chain, set_frame_range
from pipe.dcc.houdini.hipfile.shot_sets import SETS_NODE_NAME, sync_sets
from pipe.dcc.houdini.publish import load_layers
from pipe.dcc.houdini.util import nodetypes
from pipe.dcc.houdini.tractor import parms, render_send

RENDER_NETWORK_TYPE = "SKD::render_network::1.0"
RENDER_NETWORK_NAME = "render_network"
LIGHTING_PUBLISH = "lighting/publish/lighting.usd"
# Inside the Render Network; its inputs are the layers in LAYER_TOGGLES order.
SUBMIT = "tractor_submit1"
STAGE = "/stage"
# The render hip's department nulls, between which its Render Network sits.
BEGIN = "BEGIN_RENDER"
END = "END_RENDER"
# Enough for any shot's scene graph, without sending the window a whole crowd.
MAX_PRIMS = 50_000

log = logging.getLogger(__name__)


class SceneRefused(Exception):
    """Why the render hip can't be used, worded for the artist."""


@dataclass
class RenderScene:
    hip: Path
    network: hou.LopNode
    # Whether the hip was on disk before it was opened.
    existed: bool
    # The hip's modified time when it was opened, else None.
    modified: float | None
    # The lighting hip whose Render Network values a new one copied, else "".
    seeded_from: str = ""
    # What the artist should know, such as sets that were added.
    notes: list[str] = field(default_factory=list)


def open_scene(shot: Shot, hip: Path) -> RenderScene:
    """Load `shot`'s render hip from `hip`, or build it in memory when it is
    missing, with a Render Network either way. Saves nothing."""
    existed = hip.exists()
    if existed:
        load(hip)
        if (network := _find_network()) is not None:
            _place_between_nulls(network)
            notes = _prepare(shot)
            return RenderScene(hip, network, existed, _modified(hip), notes=notes)

    # A new Render Network starts from the one the lighter set up, if any,
    # which is read before this hip's own nodes replace the lighting hip's.
    seed, seeded_from = _lighting_seed(shot)
    if existed:
        load(hip)
        anchor = _stage().displayNode()
    else:
        hou.hipFile.clear(suppress_save_prompt=True)
        # $HIP, and so each layer's Render Root, comes from where it will be saved.
        hou.hipFile.setName(str(hip))
        hou.setContextOption("SHOT", shot.shot_path)
        build_department_chain(_stage(), Department.RENDER.value)
        anchor = None
    notes = _prepare(shot)
    network = _create_network(anchor)
    if seed:
        # Houdini 20 added it, after the stubs.
        network.setParmsFromData(seed)  # ty: ignore[unresolved-attribute]
    return RenderScene(hip, network, existed, _modified(hip), seeded_from, notes)


def save(scene: RenderScene) -> None:
    """Save the hip, unless it changed on disk after it was opened."""
    if _modified(scene.hip) != scene.modified:
        raise SceneRefused(
            f"{scene.hip} changed on disk after the window opened it, perhaps "
            "saved from Houdini. Nothing was saved, so that change is kept. "
            "Reload to see it, then make your edits again."
        )
    folder = scene.hip.parent
    # A shot's render folder is a link into the cache, where its renders go.
    if folder.name == RENDER_DIRNAME and folder.is_relative_to(get_production_path()):
        link_to_cache(folder)
    folder.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(str(scene.hip))
    scene.existed = True
    scene.modified = _modified(scene.hip)


def layers(network: hou.LopNode) -> list[Layer]:
    """Each render layer wired into the Render Network's Submit, with the
    folder its next Send renders into."""
    # The asset's own module names which toggle sends which Submit input.
    network.hdaModule().refresh_render_dirs(network)
    toggles: tuple[str, ...] = network.hdaModule().LAYER_TOGGLES
    found: list[Layer] = []
    for output, toggle in zip(submit(network).inputs(), toggles):
        if output is None:
            continue
        configure = render_send.chain(output).configure
        found.append(
            Layer(
                name=parms.text(configure, parms.LAYER),
                toggle=toggle,
                next_folder=parms.text(configure, parms.OUTPUT),
                last_job=parms.text(configure, parms.LAST_JOB),
            )
        )
    return found


def enabled_layers(network: hou.LopNode) -> list[hou.Node]:
    """The Submit inputs whose layers the artist turned on."""
    network.hdaModule().refresh_render_dirs(network)
    toggles: tuple[str, ...] = network.hdaModule().LAYER_TOGGLES
    return [
        output
        for output, toggle in zip(submit(network).inputs(), toggles)
        if output is not None and network.evalParm(toggle)
    ]


def prims(network: hou.LopNode) -> list[Prim]:
    """The prims the Render Network's input stage holds, parents before children."""
    upstream = network.input(0)
    if upstream is None:
        return []
    stage = upstream.stage()
    if stage is None:
        return []
    found: list[Prim] = []
    for prim in stage.Traverse():
        imageable = UsdGeom.Imageable(prim)
        purpose = imageable.GetPurposeAttr().Get() if imageable else None
        found.append(
            Prim(
                path=prim.GetPath().pathString,
                type=str(prim.GetTypeName()),
                kind=str(Usd.ModelAPI(prim).GetKind() or ""),
                purpose=""
                if purpose in (None, UsdGeom.Tokens.default_)
                else str(purpose),
            )
        )
        if len(found) >= MAX_PRIMS:
            log.warning(
                "Listed only the first %d prims of %s", MAX_PRIMS, network.path()
            )
            break
    return found


def departments(network: hou.LopNode) -> list[DepartmentLayer]:
    """The rows of the load layers node feeding the Render Network."""
    node = load_layers_node()
    if node is None:
        return []
    found = []
    for row in load_layers.rows(node):
        menu = load_layers.version_menu(node, row)
        found.append(
            DepartmentLayer(
                row=row,
                label=load_layers.row_label(node, row),
                enabled=bool(node.evalParm(load_layers.ENABLE.format(row))),
                version=str(node.evalParm(load_layers.VERSION.format(row))),
                # Houdini menus are flat: token, label, token, label.
                choices=list(zip(menu[::2], menu[1::2])),
            )
        )
    return found


def set_department(row: str, enabled: bool, version: str) -> None:
    """Turn a load layers row on or off, and pin it to `version` or CURRENT."""
    node = load_layers_node()
    if node is None:
        raise SceneRefused(
            "The render hip has no load layers node, so its department layers "
            f"can't be changed. Open {hou.hipFile.path()} in Houdini and add one."
        )
    if row not in load_layers.rows(node):
        raise SceneRefused(f"The load layers node has no {row} row.")
    # rows() found each row by these parms, so both are there.
    node.setParms(
        {
            load_layers.ENABLE.format(row): int(enabled),
            load_layers.VERSION.format(row): version,
        }
    )
    # As the node's own menus do, so a pin shows in the network.
    load_layers.show_pins(node)


def check_departments() -> None:
    """Refuse a Send whose load layers node reads a version that isn't there."""
    node = load_layers_node()
    if node is None:
        return
    if missing := load_layers.missing_pins(node):
        raise SceneRefused(f"Nothing was sent.\n\n{missing}")


def load_layers_node() -> hou.Node | None:
    found = [n for n in _stage().children() if n.type().name() == nodetypes.LOAD_LAYERS]
    return found[0] if found else None


def lighting_notes(shot: Shot) -> list[str]:
    """Warn when the lighter saved after their last publish, which this hip can't see."""
    folder = shot_folder(shot.code or "")
    publish, hip = folder / LIGHTING_PUBLISH, folder / LIGHTING_HIP
    if not publish.exists():
        return [
            "Lighting has never been published for this shot, so these renders "
            "have none of the lighter's lights. Publish from the lighting hip first."
        ]
    if hip.exists() and hip.stat().st_mtime > publish.stat().st_mtime:
        return [
            "The lighting hip was saved after lighting was last published. These "
            "renders read the publish, so any later lighting changes are missing. "
            "Publish from the lighting hip if they should be in."
        ]
    return []


def _stage() -> hou.LopNetwork:
    if not isinstance(stage := hou.node(STAGE), hou.LopNetwork):
        raise SceneRefused(
            f"The render hip has no {STAGE} network. Open it in Houdini and "
            "recreate /stage, or ask a TD."
        )
    return stage


def load(hip: Path) -> None:
    try:
        hou.hipFile.load(str(hip), suppress_save_prompt=True, ignore_load_warnings=True)
    except hou.LoadWarning:
        # The hip loaded; the warnings are in the log.
        log.warning("Loading %s warned", hip, exc_info=True)


def _prepare(shot: Shot) -> list[str]:
    """Point the hip at `shot` as opening it from the shot menu would, and
    return what the artist should know about it."""
    hou.setContextOption("SHOT", shot.shot_path)
    # Houdini takes a number too, which the stubs leave out.
    hou.setContextOption(
        "SHOT_SUBSTEPS",
        shot.substeps,  # ty: ignore[invalid-argument-type]
    )
    set_frame_range(shot)
    notes = []
    if (sets := hou.node(f"{STAGE}/{SETS_NODE_NAME}")) is not None:
        added = sync_sets(sets, shot.sets or [])
        if added:
            notes.append(
                "Added the shot's sets: " + ", ".join(s.display_name for s in added)
            )
        unpublished = [s for s in added if not current_layer_path(s.name).exists()]
        if unpublished:
            notes.append(
                "Not published yet, so the render hip won't cook until they are: "
                + ", ".join(s.display_name for s in unpublished)
            )
    return notes + lighting_notes(shot)


def _find_network() -> hou.LopNode | None:
    found = [n for n in _stage().children() if n.type().name() == RENDER_NETWORK_TYPE]
    if len(found) > 1:
        names = ", ".join(n.path() for n in found)
        raise SceneRefused(
            f"The render hip has more than one Render Network ({names}), so the "
            f"window can't tell which to show. Open {hou.hipFile.path()} in "
            "Houdini and delete all but one."
        )
    return cast(hou.LopNode, found[0]) if found else None


def _create_network(anchor: hou.Node | None) -> hou.LopNode:
    """A new Render Network between BEGIN_RENDER and END_RENDER, or below
    `anchor` in a hip without them."""
    network = cast(
        hou.LopNode,
        _stage().createNode(RENDER_NETWORK_TYPE, RENDER_NETWORK_NAME),
    )
    begin, end = _null(BEGIN), _null(END)
    if begin is not None and end is not None:
        network.setInput(0, end.input(0) or begin)
        end.setInput(0, network)
        network.setPosition(end.position())
        end.move((0, -1.5))
        end.setDisplayFlag(True)
        return network
    if anchor is not None:
        network.setInput(0, anchor)
    network.moveToGoodPosition()
    network.setDisplayFlag(True)
    return network


def _place_between_nulls(network: hou.LopNode) -> None:
    """Move a Render Network that hangs below BEGIN_RENDER or END_RENDER to
    between them, as older render hips have it. Other wiring is the artist's."""
    begin, end = _null(BEGIN), _null(END)
    if begin is None or end is None or end.input(0) == network:
        return
    # hou hands out a new object per lookup, so nodes compare with ==.
    upstream = end.input(0)
    if network.input(0) == end:
        # BEGIN_RENDER → END_RENDER → render_network
        if network in end.outputs() and len(end.outputs()) > 1:
            return
        network.setInput(0, upstream)
    elif network.input(0) != upstream:
        return
    # BEGIN_RENDER → render_network → END_RENDER
    end.setInput(0, network)
    if network.isDisplayFlagSet():
        end.setDisplayFlag(True)
    log.info("Moved %s between %s and %s", network.path(), BEGIN, END)


def _lighting_seed(shot: Shot) -> tuple[dict[str, Any] | None, str]:
    """The parms of the lighting hip's Render Network, and that hip, if it has one."""
    hip = shot_folder(shot.code or "") / LIGHTING_HIP
    if not hip.exists():
        return None, ""
    try:
        load(hip)
        candidates = [
            n for n in _stage().children() if n.type().name() == RENDER_NETWORK_TYPE
        ]
        if not candidates:
            return None, ""
        source = candidates[0]
        data = source.parmsAsData()  # ty: ignore[unresolved-attribute]
        return data, f"{hip} ({source.name()})"
    # A lighting hip that won't load only costs the artist a fresh start.
    except Exception:
        log.warning("Could not read the Render Network of %s", hip, exc_info=True)
        return None, ""
    finally:
        hou.hipFile.clear(suppress_save_prompt=True)


def submit(network: hou.LopNode) -> hou.Node:
    submit = network.node(SUBMIT)
    if submit is None:
        raise SceneRefused(
            f"{network.path()} has no {SUBMIT} inside it, so it can't send. "
            "Update the SKD Render Network asset, or ask a TD."
        )
    return submit


def _null(name: str) -> hou.LopNode | None:
    node = hou.node(f"{STAGE}/{name}")
    return node if isinstance(node, hou.LopNode) else None


def _modified(hip: Path) -> float | None:
    return hip.stat().st_mtime if hip.exists() else None
