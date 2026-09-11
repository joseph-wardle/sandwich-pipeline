"""The one USD stage a Maya assembly scene composes its pieces through."""

from __future__ import annotations

import mayaUsd.ufe
from maya import cmds as mc
from pxr import Usd, UsdGeom

from pipe.core.assembly.model import AssemblyError
from pipe.core.assembly.normalize import SOURCE_LAYER_UP_AXIS

STAGE_TRANSFORM_NAME = "assembly_stage"
STAGE_SHAPE_NAME = "assemblyStage"

_STAGE_SHAPE_TYPE = "mayaUsdProxyShape"

_STAGE_METERS_PER_UNIT = 0.01


def stage_shape() -> str | None:
    """Return the assembly's proxy shape, refusing a scene that has more than one."""
    shapes = mc.ls(type=_STAGE_SHAPE_TYPE, long=True) or []
    if len(shapes) > 1:
        raise AssemblyError(
            f"This scene has {len(shapes)} USD stages and an assembly needs exactly "
            "one. Delete the stages that do not belong to the assembly."
        )
    return shapes[0] if shapes else None


def ensure_assembly_stage() -> Usd.Stage:
    """Return the assembly's working stage, creating an empty one on first split."""
    shape = stage_shape() or _create_stage_shape()
    stage = mayaUsd.ufe.getStage(shape)
    if stage is None:
        raise AssemblyError(
            "The assembly's USD stage could not be opened. Save and reopen the "
            f"scene, then try again (proxy shape: {shape})."
        )
    return stage


def _create_stage_shape() -> str:
    """Create the assembly's stage: one empty proxy shape, in the show's units."""
    transform = mc.createNode("transform", name=STAGE_TRANSFORM_NAME)
    shape = mc.createNode(_STAGE_SHAPE_TYPE, name=STAGE_SHAPE_NAME, parent=transform)
    mc.connectAttr("time1.outTime", f"{shape}.time")

    resolved = stage_shape()
    if resolved is None:
        raise AssemblyError(
            "Could not create the assembly's USD stage. Check that the "
            "mayaUsdPlugin is loaded, then try again."
        )

    stage = mayaUsd.ufe.getStage(resolved)
    UsdGeom.SetStageMetersPerUnit(stage, _STAGE_METERS_PER_UNIT)
    UsdGeom.SetStageUpAxis(stage, SOURCE_LAYER_UP_AXIS)
    return resolved
