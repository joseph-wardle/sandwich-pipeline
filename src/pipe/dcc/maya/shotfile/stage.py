"""The USD stage a Maya shot scene composes: one proxy shape over a scene-owned layer."""

from __future__ import annotations

import logging

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


def build_shot_stage(shot: Shot) -> Usd.Stage:
    """Give the open scene a new, empty shot stage and stamp it with the shot code."""
    transform = mc.createNode("transform", name="stage_transform")
    # With no `filePath`, the proxy composes an in-memory stage over an anonymous root.
    stage_shape = mc.createNode("mayaUsdProxyShape", name="stage", parent=transform)
    mc.connectAttr("time1.outTime", f"{stage_shape}.time")
    mc.fileInfo("code", shot.code or "")
    # The stage this call made, never one looked up afterwards: the scene may hold
    # other proxies.
    return mayaUsd.ufe.getStage(mc.ls(stage_shape, long=True)[0])


__all__ = [
    "add_sublayer",
    "build_shot_stage",
    "get_stage",
    "get_stage_shape",
]
