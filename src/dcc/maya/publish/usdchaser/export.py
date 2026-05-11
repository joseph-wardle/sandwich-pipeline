from __future__ import annotations

import logging
import re
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
import mayaUsd.lib as mayaUsdLib
from pxr import Sdf, Usd

from core.asset import paths_for_asset

from .utils import (
    find_and_move_prim,
    make_topo_attrs_default,
    path_to_maya_dag_map,
    prefix_material_bindings,
    scale_down_geo,
    split_by_namespace,
    split_preroll,
)

if TYPE_CHECKING:
    from typing import Protocol

    class TimeSampleble(Protocol):
        def GetTimeSamples(self) -> list[float]: ...

        def GetNumTimeSamples(self) -> int: ...


from env_sg import DB_Config

from core.shotgrid import ShotGrid
from core.struct.timeline import Timeline
from core.util import log_errors

log = logging.getLogger(__name__)

ANIM_CLASS_PATH = Sdf.Path("/__class__/anim")
RIG_SCOPE_PATH = Sdf.Path("/rig")
RIG_ROOT_PATH = Sdf.Path("/rig")
RIG_GEO_PATH = Sdf.Path("/rig/geo")


class ExportChaserMode(IntEnum):
    ANIM = 1
    CAM = 2
    RIG = 3
    SPLINE_ANIM = 4


@attrs.define
class ChaserArgs:
    mode: ExportChaserMode = attrs.field(converter=lambda v: ExportChaserMode(int(v)))
    timeline: Timeline | None = attrs.field(
        default=None,
        kw_only=True,
        converter=lambda t: Timeline.from_json(t) if t else None,
    )


class ExportChaser(mayaUsdLib.ExportChaser):
    ID: str = "SKD"

    _chaser_args: ChaserArgs
    _dag_to_usd: mayaUsdLib.DagToUsdMap
    _stage: Usd.Stage

    def __init__(self, factoryContext, *args, **kwargs) -> None:
        super(ExportChaser, self).__init__(factoryContext, *args, **kwargs)

        self._dag_to_usd = factoryContext.GetDagToUsdMap()
        self._stage = factoryContext.GetStage()
        self.job_args = factoryContext.GetJobArgs()
        self._chaser_args = ChaserArgs(**self.job_args.allChaserArgs[self.ID])

    @log_errors
    def PostExport(self) -> bool:
        if self._chaser_args.mode == ExportChaserMode.ANIM:
            self._post_export_anim()
        elif self._chaser_args.mode == ExportChaserMode.SPLINE_ANIM:
            self._post_export_anim(suffix="spline")
        elif self._chaser_args.mode == ExportChaserMode.RIG:
            self._post_export_rig()
        elif self._chaser_args.mode == ExportChaserMode.CAM:
            self._post_export_cam()
        else:
            raise ValueError(
                f"{self._chaser_args.mode} is not a valid SKD chaser mode."
            )
        return True

    def _post_export_anim(self, suffix: str | None = None):
        assert self._chaser_args.timeline is not None
        path_dag_mapping = path_to_maya_dag_map(self._dag_to_usd)

        scale_down_geo(self._stage)
        make_topo_attrs_default(self._stage)
        namespace_layer_suffix = "anim" if not suffix else f"{suffix}.anim"
        layers = split_by_namespace(
            self._stage, namespace_layer_suffix, path_dag_mapping
        )
        root_layer = self._stage.GetRootLayer()

        conn = ShotGrid.connect(DB_Config)

        for namespace, layer in layers.items():
            # Try and get the name of the rig from the namespace (strip trailing digits in case of multiple of the same rig in one scene)
            # TODO: Make this more robust by querying for asset metadata on the rig instead of guessing from the namespace.
            rig_name = re.sub(r"\d+$", "", namespace)

            # The path to the root of the animated geometry.
            preroll_name = namespace if not suffix else f"{namespace}.{suffix}"
            stitched_layer = split_preroll(
                layer, preroll_name, RIG_GEO_PATH, self._chaser_args.timeline
            )

            # Create prim that will hold the animation and be inherited by the rig in shots.
            # Eg. /__class__/anim/rig_namespace
            namespace_anim_path = ANIM_CLASS_PATH.AppendChild(namespace)
            anim_prim_spec = Sdf.CreatePrimInLayer(root_layer, namespace_anim_path)
            anim_prim_spec.specifier = Sdf.SpecifierClass

            anim_reference = Sdf.Reference(
                Sdf.ComputeAssetPathRelativeToLayer(
                    root_layer, stitched_layer.realPath
                ),
                RIG_ROOT_PATH,
            )
            anim_prim_spec.referenceList.Append(anim_reference)

            # Attempt to get the path of the published rig USD to reference
            rig_usd_filepath: Path | None = None
            try:
                asset = conn.get_asset(name=rig_name)
                asset_paths = paths_for_asset(asset)
                rig_usd_filepath = asset_paths.rig_path / "usd/main.usd"
            except Exception:
                log.error(
                    f"[chaser] couldn't determine asset for rig in namespace '{namespace}'. "
                    f"asset={getattr(asset, 'asset_path', None)} rig_path={rig_usd_filepath}",
                    exc_info=True,
                )

            # The rig scope needs to be defined not just an "over"
            rig_prim_spec = Sdf.CreatePrimInLayer(root_layer, RIG_SCOPE_PATH)
            rig_prim_spec.specifier = Sdf.SpecifierDef
            rig_prim_spec.typeName = "Scope"

            # Define the rig and have it inherit the animation
            instance_prim_path = RIG_SCOPE_PATH.AppendChild(namespace)
            instance_prim_spec = Sdf.CreatePrimInLayer(root_layer, instance_prim_path)
            instance_prim_spec.specifier = Sdf.SpecifierDef
            instance_prim_spec.inheritPathList.Prepend(
                ANIM_CLASS_PATH.AppendChild(namespace)
            )

            # Reference the rig USD so we have materials, CFX, etc
            rig_prim_path = RIG_SCOPE_PATH.AppendChild(rig_name)
            if rig_usd_filepath:
                rig_relative_usd_filepath = Sdf.ComputeAssetPathRelativeToLayer(
                    root_layer, rig_usd_filepath.as_posix()
                )
                instance_reference = Sdf.Reference(
                    rig_relative_usd_filepath, rig_prim_path
                )
                instance_prim_spec.referenceList.Append(instance_reference)
            else:
                log.error(
                    f"[chaser] had no asset path for the rig in namespace '{namespace}'. "
                    "It was not referenced into the scene, it may still appear but will be improperly configured. "
                    "Please talk to the rigging team and let them know."
                )

    def _post_export_rig(self):
        scale_down_geo(self._stage)
        prefix_material_bindings(self._stage, RIG_GEO_PATH, "MAT_")
        # We want the bindings for later in the pipeline when we assemble the rig USD,
        # but we'll remove the materials authored in Maya since we only want the bindings
        self._stage.RemovePrim(RIG_ROOT_PATH.AppendChild("mtl"))

    def _post_export_cam(self):
        # We don't scale down the camera here because we need to import it
        # back into Maya. Instead we'll scale it down when we import it into
        # Solaris.

        new_shotCam_path = Sdf.Path("/LnD_shotCam")
        find_and_move_prim(
            self._stage.GetEditTarget().GetLayer(), "world_CTRL", new_shotCam_path
        )
        self._stage.SetDefaultPrim(self._stage.GetPrimAtPath(new_shotCam_path))
