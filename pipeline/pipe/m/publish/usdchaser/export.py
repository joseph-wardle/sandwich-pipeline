from __future__ import annotations

import logging
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import attrs
import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]
from pxr import Sdf, Usd

from pipe.asset import paths_for_asset

from .utils import (
    find_and_move_prim,
    make_topo_attrs_default,
    path_to_maya_dag_map,
    scale_down_geo,
    split_by_namespace,
    split_preroll,
    update_material_bindings,
)

if TYPE_CHECKING:
    from typing import Protocol

    class TimeSampleble(Protocol):
        def GetTimeSamples(self) -> list[float]: ...

        def GetNumTimeSamples(self) -> int: ...


from env_sg import DB_Config

from pipe.db import DB
from pipe.struct.timeline import Timeline
from pipe.util import log_errors

log = logging.getLogger(__name__)


class ExportChaserMode(IntEnum):
    ANIM = 1
    CAM = 2
    RIG = 3
    SPLINE_ANIM = 4


@attrs.define
class ChaserArgs:
    mode: ExportChaserMode = attrs.field(converter=int)
    timeline: Optional[Timeline] = attrs.field(
        default=None,
        kw_only=True,
        converter=lambda t: Timeline.from_json(t) if t else None,
    )


class ExportChaser(mayaUsdLib.ExportChaser):
    ID: str = "lnd"

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
                f"{self._chaser_args.mode} is not a valid LnD chaser mode."
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

        conn = DB.Get(DB_Config)

        for name, layer in layers.items():
            # Try and get the name of the rig from the namespace (strip trailing digit in case of multiple of the same rig in one scene)
            # TODO: Make this more robust by querying for asset metadata on the rig instead of guessing from the namespace.
            base_name = ""
            if name[-1].isdigit():
                base_name = name[:-1]
            else:
                base_name = name

            # The path to the root of the animated geometry.
            rig_geo_prim_path = Sdf.Path("/rig/geo")
            preroll_name = name if not suffix else f"{name}.{suffix}"
            stitched_layer = split_preroll(
                layer, preroll_name, rig_geo_prim_path, self._chaser_args.timeline
            )

            # Create prim that will hold the animation and be inherited by the rig in shots.
            anim_class_path = Sdf.Path(f"__class__/anim/{name}")
            anim_prim_spec = Sdf.CreatePrimInLayer(root_layer, anim_class_path)
            anim_prim_spec.specifier = Sdf.SpecifierOver

            anim_reference = Sdf.Reference(
                Sdf.ComputeAssetPathRelativeToLayer(
                    root_layer, stitched_layer.realPath
                ),
                rig_geo_prim_path,
            )
            anim_prim_spec.referenceList.appendedItems = [anim_reference]

            # Attempt to reference in the published rig as a sublayer
            relative_rig_path: Path | None = None
            relative_path_str: str | None
            try:
                asset = conn.get_asset_by_name(base_name)
                asset_paths = paths_for_asset(asset)
                rig_path = asset_paths.rig_path / "usd/main.usd"
                relative_path_str = Sdf.ComputeAssetPathRelativeToLayer(
                    root_layer, rig_path.as_posix()
                )
                if relative_path_str not in root_layer.subLayerPaths:  # type: ignore
                    root_layer.subLayerPaths.append(relative_path_str)
                log.info(f"added rig sublayer for {name}: {relative_path_str}")

            except Exception:
                log.error(
                    f"[chaser] asset link failed for {name} (base={base_name})"
                    f"asset={getattr(asset, 'asset_path', None)} rig_path={rig_path if 'rig_path' in locals() else None}"
                    f"relative_path={relative_path_str} root_layer={root_layer.realPath}",
                    exc_info=True,
                )

            # (Currently) hacky handling for when we have multiple rigs of the same type.
            if name != base_name and relative_path_str:
                # Create a concrete rig instance for this namespace so the
                # class-based clips can bind to a real prim.
                character_parent = Sdf.CreatePrimInLayer(
                    root_layer, Sdf.Path("/character")
                )
                character_parent.specifier = Sdf.SpecifierOver

                instance_prim_path = Sdf.Path(f"/character/{name}")
                instance_prim_spec = Sdf.CreatePrimInLayer(
                    root_layer, instance_prim_path
                )
                instance_prim_spec.specifier = Sdf.SpecifierDef

                rig_prim_path = Sdf.Path(f"/character/{base_name}")
                instance_reference = Sdf.Reference(relative_path_str, rig_prim_path)
                instance_prim_spec.referenceList.appendedItems = [instance_reference]
                instance_prim_spec.inheritPathList.prependedItems = [
                    Sdf.Path(f"/__class__/anim/{name}")
                ]

    def _post_export_rig(self):
        scale_down_geo(self._stage)
        update_material_bindings(self._stage, "/rig", "/rig/geo", "MAT_")

    def _post_export_cam(self):
        # We don't scale down the camera here because we need to import it
        # back into Maya. Instead we'll scale it down when we import it into
        # Solaris.

        new_shotCam_path = Sdf.Path("/LnD_shotCam")
        find_and_move_prim(
            self._stage.GetEditTarget().GetLayer(), "world_CTRL", new_shotCam_path
        )
        self._stage.SetDefaultPrim(self._stage.GetPrimAtPath(new_shotCam_path))
