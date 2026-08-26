"""The USD scaffold a Maya shot scene composes: proxy shape, root layer, environment."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import maya.cmds as mc
import mayaUsd  # type: ignore[import-not-found]
from pxr import Sdf, Tf, Usd, UsdGeom

from pipe.core.shotgrid import Environment, Shot, build_shot_path
from pipe.core.util.paths import get_production_path

log = logging.getLogger(__name__)

ROOT_LAYER = "maya_root.usd"
_MAYA_OVERRIDE = "maya_override.usd"

# Sets are authored in metres and Maya shot scenes are in centimetres. The stage is
# unit-mixed so the conversion is scoped to the prim the pipeline owns rather than
# applied stage-wide.
_SETS_PRIM = Sdf.Path("/sets")
_SET_SCALE = (100.0, 100.0, 100.0)


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
    add_sets(shot)
    # Last, so the scene's edit target is left on the override layer.
    _target_override_layer(stage, stage.GetRootLayer(), shot)


def add_sets(shot: Shot) -> None:
    """Payload the shot's linked sets under `/sets`, converted to centimetres."""
    stage = get_stage()
    with Usd.EditContext(stage, stage.GetRootLayer()):
        stage.RemovePrim(_SETS_PRIM)
        UsdGeom.Xform.Define(stage, _SETS_PRIM).AddScaleOp().Set(_SET_SCALE)
        for env in linked_environments(shot):
            _payload_set(stage, env)


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


def _payload_set(stage: Usd.Stage, env: Environment) -> None:
    # `environment_path` names a directory; the layer is always its `main.usd`. The
    # path is authored verbatim, resolved by `PXR_AR_DEFAULT_SEARCH_PATH`.
    asset_path = f"{env.environment_path}/main.usd"
    set_layer = Sdf.Layer.FindOrOpen(asset_path)
    if not set_layer:
        log.warning("Could not open set layer at %s", asset_path)
        return
    scope = _SETS_PRIM.AppendChild(Tf.MakeValidIdentifier(Path(asset_path).parent.name))
    for name in set_layer.rootPrims.keys():
        prim = stage.DefinePrim(scope.AppendChild(name))
        prim.GetPayloads().AddPayload(
            asset_path, Sdf.Path.absoluteRootPath.AppendChild(name)
        )


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


def linked_environments(shot: Shot) -> list[Environment]:
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
    "add_sets",
    "add_sublayer",
    "build_shot_stage",
    "create_stage_proxy",
    "get_stage",
    "get_stage_shape",
    "linked_environments",
    "serialize_usd_edits_into_scene",
    "setup_environment",
    "shot_override_layer_path",
]
