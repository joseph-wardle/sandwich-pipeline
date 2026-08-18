from __future__ import annotations

import logging
import re
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
import mayaUsd.lib as mayaUsdLib
from pxr import Usd

from pipe.core.asset import paths_for_asset

from ..anim_index import (
    AnimStream,
    PublishedAnim,
    RigReference,
    author_rig_entry,
    entries_from_json,
    verify_keepable,
)
from ..prim_paths import RIG_GEO_PATH, RIG_ROOT_PATH, RIG_SCOPE_PATH, SHOT_CAM_PATH
from .utils import (
    clear_root_prims,
    flatten_camera,
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

from pipe.core.shotgrid import ShotGrid
from pipe.core.struct.timeline import Timeline
from pipe.core.util import log_errors

log = logging.getLogger(__name__)


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
    keep: tuple[PublishedAnim, ...] = attrs.field(
        default=(),
        kw_only=True,
        converter=lambda k: entries_from_json(k) if k else (),
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
        match self._chaser_args.mode:
            case ExportChaserMode.ANIM:
                self._post_export_anim(AnimStream.MAIN)
            case ExportChaserMode.SPLINE_ANIM:
                self._post_export_anim(AnimStream.SPLINE)
            case ExportChaserMode.RIG:
                self._post_export_rig()
            case ExportChaserMode.CAM:
                self._post_export_cam()
        return True

    def _post_export_anim(self, stream: AnimStream) -> None:
        assert self._chaser_args.timeline is not None
        # First, so that a rig the artist kept whose animation has since gone
        # missing costs nothing but the message.
        verify_keepable(self._chaser_args.keep)
        if not self._stage.GetPseudoRoot().GetChildren():
            raise RuntimeError(
                "The animation export produced nothing, so the shot's publish was "
                "left as it was. The rigs that were published have no geometry to "
                "export."
            )

        path_dag_mapping = path_to_maya_dag_map(self._dag_to_usd)

        scale_down_geo(self._stage)
        make_topo_attrs_default(self._stage)
        layers = split_by_namespace(
            self._stage, stream.anim_layer_suffix, path_dag_mapping
        )
        conn = ShotGrid.connect(DB_Config)

        # Everything that can fail happens here, while the shot's previous index
        # is still on disk. Stitching is the step that raises on a rig whose
        # geometry does not meet the convention, and a rig that has never
        # resolved to a ShotGrid asset is indexed unbound rather than refused.
        published = [
            (
                name,
                Path(
                    split_preroll(
                        layer,
                        stream.stitched_layer_name(name),
                        RIG_GEO_PATH,
                        self._chaser_args.timeline,
                    ).realPath
                ),
                _rig_reference(conn, name),
            )
            for name, layer in layers.items()
        ]

        for kept in self._chaser_args.keep:
            if kept.rig is None:
                log.warning(
                    "[chaser] '%s' was published before the index named rigs, so "
                    "keeping it carries its animation forward with no rig, and it "
                    "will appear downstream without materials or CFX",
                    kept.name,
                )

        # Past this line the shot's index is gone until it is rewritten, so
        # nothing below may touch the scene, ShotGrid, or the filesystem.
        root_layer = self._stage.GetRootLayer()
        clear_root_prims(root_layer)
        for name, anim_layer, rig in published:
            author_rig_entry(root_layer, name, anim_layer, rig)
        for kept in self._chaser_args.keep:
            author_rig_entry(root_layer, kept.name, kept.anim_layer, kept.rig)
        root_layer.Save()

    def _post_export_rig(self):
        scale_down_geo(self._stage)
        prefix_material_bindings(self._stage, RIG_GEO_PATH, "MAT_")
        # We want the bindings for later in the pipeline when we assemble the rig USD,
        # but we'll remove the materials authored in Maya since we only want the bindings
        self._stage.RemovePrim(RIG_ROOT_PATH.AppendChild("mtl"))

    def _post_export_cam(self) -> None:
        # We don't scale down the camera here because we need to import it
        # back into Maya. Instead we'll scale it down when we import it into
        # Solaris.
        flatten_camera(self._stage, SHOT_CAM_PATH)


def _rig_reference(conn: ShotGrid, name: str) -> RigReference | None:
    """The published rig a freshly exported rig's animation belongs to."""
    # Trailing digits distinguish two of the same rig in one scene.
    # TODO: Make this more robust by querying for asset metadata on the rig
    # instead of guessing from the namespace.
    rig_name = re.sub(r"\d+$", "", name)
    try:
        asset_paths = paths_for_asset(conn.get_asset(name=rig_name))
    except Exception:
        log.exception(
            "[chaser] could not find a rig asset named '%s' for the rig '%s'. "
            "Please talk to the rigging team and let them know.",
            rig_name,
            name,
        )
        return None
    return RigReference(
        asset_path=asset_paths.rig_path / "usd/main.usd",
        prim_path=RIG_SCOPE_PATH.AppendChild(rig_name).pathString,
    )
