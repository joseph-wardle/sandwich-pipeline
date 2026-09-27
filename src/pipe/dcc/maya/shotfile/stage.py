"""The USD stage a Maya shot scene composes: one proxy shape over a scene-owned layer.

The proxy's root layer is anonymous and saved inside the `.mb` (ADR-0024), so USD
edits made in one scene stay in that scene, never render and never reach Houdini.
"""

from __future__ import annotations

import logging
from typing import Callable

import maya.cmds as mc
import mayaUsd  # type: ignore[import-not-found]
from pxr import Sdf, Usd

from pipe.core.shotgrid import Shot

log = logging.getLogger(__name__)


def get_stage_shape() -> str:
    """The stage this pipeline built for the open scene."""
    shapes = [
        shape
        for shape in mc.ls(type="mayaUsdProxyShape", long=True) or []
        if not mc.referenceQuery(shape, isNodeReferenced=True)
    ]
    if not shapes:
        raise RuntimeError("No USD stage found in scene")
    if len(shapes) > 1:
        # `mc.ls` order carries no meaning, so a second proxy makes this a coin flip.
        log.warning("Scene has %d USD stages; using %s", len(shapes), shapes[0])
    return str(shapes[0])


def get_stage() -> Usd.Stage:
    return mayaUsd.ufe.getStage(get_stage_shape())


def add_sublayer(root_layer: Sdf.Layer, layer: Sdf.Layer) -> None:
    """Sublayer `layer` in the weakest position, if it isn't already present."""
    if layer.identifier not in root_layer.subLayerPaths:  # type: ignore[operator]
        root_layer.subLayerPaths.append(layer.identifier)


def create_stage_proxy() -> None:
    """Create the scene's `mayaUsdProxyShape` over a new anonymous root layer."""
    transform = mc.createNode("transform", name="stage_transform")
    # With no `filePath`, the proxy composes an in-memory stage over an anonymous root.
    stage_shape = mc.createNode("mayaUsdProxyShape", name="stage", parent=transform)
    mc.connectAttr("time1.outTime", f"{stage_shape}.time")


def serialize_usd_edits_into_scene() -> None:
    """Keep USD edits in the Maya file itself, without prompting on save.

    These are user preferences, so every pipeline scene sets them on open: with
    Maya's default, saving asks the artist where to write the anonymous root layer.
    """
    mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocationPrompt", 0))
    mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocation", 2))


def build_shot_stage(shot: Shot, *, populate: Callable[[], object]) -> None:
    """Build a shot scene's USD stage and stamp the scene with the shot code.

    Sublayer order is strength order, so `populate` decides where the layers it
    adds land.
    """
    create_stage_proxy()
    populate()
    serialize_usd_edits_into_scene()
    mc.fileInfo("code", shot.code or "")


__all__ = [
    "add_sublayer",
    "build_shot_stage",
    "create_stage_proxy",
    "get_stage",
    "get_stage_shape",
    "serialize_usd_edits_into_scene",
]
