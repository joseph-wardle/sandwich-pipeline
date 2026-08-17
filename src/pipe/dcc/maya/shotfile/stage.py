"""The USD scaffold a Maya shot scene composes: proxy shape, root layer, environment."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import maya.cmds as mc
import mayaUsd  # type: ignore[import-not-found]
from pxr import Sdf, Usd, UsdGeom

from pipe.core.shotgrid import Environment, Shot, build_shot_path
from pipe.core.util.paths import get_production_path

log = logging.getLogger(__name__)

ROOT_LAYER = "maya_root.usd"
_MAYA_OVERRIDE = "maya_override.usd"

# Environment USD is authored in metres and Maya shot scenes are in centimetres.
_ENVIRONMENT_SCALE = (100.0, 100.0, 100.0)


def get_stage_shape() -> str:
    shapes = mc.ls(type="mayaUsdProxyShape", long=True) or []
    if not shapes:
        raise RuntimeError("No USD stage found in scene")
    if len(shapes) > 1:
        # `mc.ls` order carries no meaning, so a second proxy makes this a coin flip.
        log.warning("Scene has %d USD stages; using %s", len(shapes), shapes[0])
    return str(shapes[0])


def get_stage() -> Usd.Stage:
    return mayaUsd.ufe.getStage(get_stage_shape())


def shot_override_layer_path(shot_code: str) -> str:
    """The shot's override layer, relative to the production root."""
    return "/".join((build_shot_path(shot_code), "set", _MAYA_OVERRIDE))


def add_sublayer(root_layer: Sdf.Layer, layer: Sdf.Layer) -> None:
    """Sublayer `layer` in the weakest position, if it isn't already present."""
    if layer.identifier not in root_layer.subLayerPaths:  # type: ignore[operator]
        root_layer.subLayerPaths.append(layer.identifier)


def create_stage_proxy(
    root_layer_path: Path, *, file_path_ref: str
) -> tuple[Sdf.Layer, bool]:
    """Create the scene's `mayaUsdProxyShape` over `root_layer_path`."""
    transform = mc.createNode("transform", name="stage_transform")
    mc.createNode("mayaUsdProxyShape", name="stage", parent=transform)
    stage_shape = get_stage_shape()
    mc.connectAttr("time1.outTime", f"{stage_shape}.time")

    created = not root_layer_path.exists()
    root_layer = Sdf.Layer.FindOrOpen(str(root_layer_path)) or Sdf.Layer.CreateNew(
        str(root_layer_path)
    )
    # A root layer built earlier in this Maya session is still locked, and a locked
    # layer refuses to save; unlock it so this scene's edits can be flushed.
    root_layer.SetPermissionToSave(True)
    if created:
        # The proxy has nothing to compose until the layer exists on disk.
        root_layer.Save()
    mc.setAttr(f"{stage_shape}.filePath", file_path_ref, type="string")
    return root_layer, created


def setup_environment(shot: Shot) -> None:
    stage = get_stage()
    root_layer = stage.GetRootLayer()

    _scale_environment(stage, root_layer)
    # The override layer is sublayered ahead of the sets so it outranks them.
    _target_override_layer(stage, root_layer, shot)
    sublayer_environments(shot)


def sublayer_environments(shot: Shot) -> None:
    """Sublayer the shot's linked sets, read-only."""
    root_layer = get_stage().GetRootLayer()
    for env in _linked_environments(shot):
        env_path = env.environment_path
        env_layer = Sdf.Layer.FindOrOpenRelativeToLayer(root_layer, env_path)
        if not env_layer:
            log.warning("Could not open environment layer at %s", env_path)
            continue
        add_sublayer(root_layer, env_layer)
        env_layer.SetPermissionToSave(False)


def serialize_usd_edits_into_scene() -> None:
    """Keep USD edits in the Maya file itself, without prompting on save."""
    mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocationPrompt", 0))
    mc.optionVar(intValue=("mayaUsd_SerializedUsdEditsLocation", 2))


def build_shot_stage(shot: Shot, *, populate: Callable[[], None]) -> None:
    """Build a shot scene's USD scaffold and stamp the scene with the shot code.

    Sublayer order is strength order, so `populate` — not this function — decides
    where the environment lands relative to layers the caller adds itself.
    """
    root_layer, _ = create_stage_proxy(
        get_production_path() / shot.shot_path / ROOT_LAYER,
        file_path_ref="../" + ROOT_LAYER,
    )

    populate()

    root_layer.Save()
    root_layer.SetPermissionToSave(False)
    serialize_usd_edits_into_scene()
    mc.fileInfo("code", shot.code or "")


def _scale_environment(stage: Usd.Stage, root_layer: Sdf.Layer) -> None:
    stage.SetEditTarget(Usd.EditTarget(root_layer))
    env_xformable = UsdGeom.Xformable(stage.OverridePrim(Sdf.Path("/environment")))
    env_xformable.ClearXformOpOrder()
    env_xformable.AddScaleOp().Set(_ENVIRONMENT_SCALE)


def _target_override_layer(stage: Usd.Stage, root_layer: Sdf.Layer, shot: Shot) -> None:
    # `CreateNew` truncates an existing file, so the shot's layout overrides only
    # survive a second scene if the layer is opened rather than recreated.
    override_path = str(
        get_production_path() / shot_override_layer_path(shot.code or "")
    )
    override_layer = Sdf.Layer.FindOrOpen(override_path) or Sdf.Layer.CreateNew(
        override_path
    )
    if not override_layer:
        log.warning("Unable to create or open shot override layer.")
        return
    override_layer.Save()
    add_sublayer(root_layer, override_layer)
    stage.SetEditTarget(Usd.EditTarget(override_layer))


def _linked_environments(shot: Shot) -> list[Environment]:
    """The shot's sets, falling back to the one linked on its sequence.

    These arrive partial from ShotGrid; reading `environment_path` lazy-fetches.
    """
    envs = [env for env in (shot.sets or []) if env is not None]
    if envs:
        return envs
    sequence = shot.sequence
    sole_env = shot.set or (sequence.set if sequence else None)
    return [sole_env] if sole_env else []


__all__ = [
    "ROOT_LAYER",
    "add_sublayer",
    "build_shot_stage",
    "create_stage_proxy",
    "get_stage",
    "get_stage_shape",
    "serialize_usd_edits_into_scene",
    "setup_environment",
    "shot_override_layer_path",
    "sublayer_environments",
]
