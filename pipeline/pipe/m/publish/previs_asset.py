from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

import maya.api.OpenMaya as om
import maya.cmds as cmds
from pxr import Gf, Sdf, Usd, UsdGeom, Vt
from Qt.QtWidgets import QWidget

from pipe.db import DB

if TYPE_CHECKING:
    from typing import Any, Sequence
from shared.util import get_production_path

from pipe.glui.dialogs import FilteredListDialog, MessageDialog
from pipe.struct.db import Asset, SGEntity

from .publisher import Publisher

log = logging.getLogger(__name__)


class PublishPrevisAssetDialog(FilteredListDialog):
    def __init__(
        self, parent: QWidget | None, items: Sequence[str], conn: Optional[DB]
    ) -> None:
        super().__init__(
            parent,
            items,
            "Publish Asset",
            "Select asset to publish",
            accept_button_name="Publish",
        )

        self._conn = conn

    def get_selected_item(self) -> str | None:
        selected_items = self._list_widget.selectedItems()
        if selected_items:
            return selected_items[0].text()
        return None


class PrevisAssetPublisher(Publisher):
    _override: bool

    def __init__(self) -> None:
        super().__init__(PublishPrevisAssetDialog)

    def _get_entity_list(self) -> list[str]:
        return self._conn.get_asset_display_name_list(sorted=True)

    def _get_entity_from_name(self, display_name: str) -> SGEntity | None:
        return self._conn.get_asset_by_display_name(display_name)

    def _get_save_path(self) -> Path | None:
        cast(PublishPrevisAssetDialog, self._dialog)
        asset = cast(Asset, self._entity)
        try:
            assert asset.path is not None
            return Path(get_production_path() / asset.path / "previs.usd")

        except AssertionError:
            error = MessageDialog(
                self._window,
                "Error: No path for this Asset set in ShotGrid. Nothing exported",
                "Error",
            )
            error.exec_()
            return None

    def _presave(self) -> bool:
        return True

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        return {
            "shadingMode": "useRegistry",
        }

    @staticmethod
    def fill_mesh_from_selection(usd_mesh):
        selection = cmds.ls(selection=True, dag=True, type="mesh")
        # cmds.scale(0.01, 0.01, 0.01)
        if not selection:
            raise RuntimeError("No mesh selected in Maya.")

        # Get MFnMesh for selected mesh
        sel_list = om.MSelectionList()
        sel_list.add(selection[0])
        dag_path = sel_list.getDagPath(0)
        mesh_fn = om.MFnMesh(dag_path)

        # Points
        points = mesh_fn.getPoints(space=om.MSpace.kWorld)
        usd_points = Vt.Vec3fArray([Gf.Vec3f(p.x, p.y, p.z) for p in points])
        usd_mesh.CreatePointsAttr(usd_points)

        # Face vertex counts and indices
        counts, indices = mesh_fn.getVertices()
        usd_mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
        usd_mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))

        # Normals
        normals = mesh_fn.getNormals(space=om.MSpace.kWorld)
        usd_normals = Vt.Vec3fArray([Gf.Vec3f(n.x, n.y, n.z) for n in normals])
        usd_mesh.CreateNormalsAttr(usd_normals)
        usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)

        # UVs
        try:
            u_array, v_array = mesh_fn.getUVs()
            uv_indices = mesh_fn.getAssignedUVs()[1]
            uv_coords = [Gf.Vec2f(u_array[i], v_array[i]) for i in uv_indices]
            usd_mesh.CreatePrimvar(
                "st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying"
            ).Set(Vt.Vec2fArray(uv_coords))
        except Exception as e:
            print(f"No UVs found, skipping. {e}")

    # Needed to add previs variant to usd, if it already exists
    def publish(self):
        super().publish()
        asset = cast(Asset, self._entity)

        try:
            check_path = os.path.join(
                get_production_path(), asset.path, "export", "payload.usdc"
            )
            print(check_path)
            assert not os.path.exists(check_path)

        except AssertionError:
            error = MessageDialog(
                self._window,
                "Error: Asset already exists, publish in Houdini",
                "Error",
            )
            error.exec_()
            return None

        stage_path = Path(
            get_production_path() / asset.path / "export" / (asset.name + ".usd")
        )

        print(f"STAGE PATH: {stage_path}")

        if os.path.exists(stage_path):
            os.remove(stage_path)

        layer = Sdf.Layer.Find(str(stage_path))

        if not layer:
            layer = Sdf.Layer.CreateNew(str(stage_path))
        else:
            layer.Clear()
            layer.Save()

        stage = Usd.Stage.Open(layer)

        # Root prim
        root_xform = UsdGeom.Xform.Define(stage, f"/{asset.name}")

        # geo
        UsdGeom.Scope.Define(stage, f"/{asset.name}/geo")

        UsdGeom.Scope.Define(stage, f"/{asset.name}/mtl")

        # proxy
        UsdGeom.Scope.Define(stage, f"/{asset.name}/geo/proxy")

        # Exported mesh should go here
        mesh = UsdGeom.Mesh.Define(stage, f"/{asset.name}/geo/proxy/geo")

        PrevisAssetPublisher.fill_mesh_from_selection(mesh)

        stage.SetDefaultPrim(root_xform.GetPrim())
        stage.GetRootLayer().Save()
