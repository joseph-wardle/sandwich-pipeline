from __future__ import annotations

import json
import traceback
from enum import IntEnum
from math import isclose
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import attrs
import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]
import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Protocol

    class TimeSampleble(Protocol):
        def GetTimeSamples(self) -> list[float]: ...

        def GetNumTimeSamples(self) -> int: ...


from env_sg import DB_Config
from shared.util import get_production_path

from pipe.db import DB
from pipe.struct.db import Asset
from pipe.struct.timeline import Timeline
from pipe.util import log_errors


class ChaserMode(IntEnum):
    ANIM = 1
    CAM = 2
    CHAR = 3


def get_frames_from_attr(attr: TimeSampleble) -> Iterable[Usd.TimeCode]:
    return (
        (Usd.TimeCode(f) for f in attr.GetTimeSamples())
        if attr.GetNumTimeSamples()
        else (Usd.TimeCode.Default(),)
    )


def create_or_clear_layer(path: str) -> Sdf.Layer:
    layer = Sdf.Layer.FindOrOpen(path)
    if layer:
        layer.Clear()
    else:
        layer = Sdf.Layer.CreateNew(path)
    return layer


def scale_down_geo(stage: Usd.Stage, scale_factor: float = 0.01) -> None:
    """Recurse through the stage and scale down all Mesh and BasisCurves prims by
    `scale_factor`"""

    root_prim = stage.GetPseudoRoot()
    data: Any

    for prim in (it := iter(Usd.PrimRange(root_prim))):
        extent = prim.GetAttribute(UsdGeom.Tokens.extent)
        if extent.IsValid():
            for frame in get_frames_from_attr(extent):
                data = np.array(extent.Get(frame))
                data *= scale_factor
                extent.Set(Vt.Vec3fArray.FromNumpy(data), frame)  # type: ignore[arg-type]

        xformable = UsdGeom.Xformable(prim)
        xformop: UsdGeom.XformOp
        for xformop in xformable.GetOrderedXformOps():
            xform_type = UsdGeom.XformOp.GetOpTypeToken(xformop.GetOpType())

            if (not xformop.IsDefined()) or xformop.IsInverseOp():
                continue

            if xform_type == UsdGeom.XformOpTypes.translate:
                for frame in get_frames_from_attr(xformop):
                    translate_data: Gf.Vec3d = xformop.Get(frame)
                    translate_data *= scale_factor
                    xformop.Set(translate_data, frame)

            elif xform_type == UsdGeom.XformOpTypes.transform:
                for frame in get_frames_from_attr(xformop):
                    matrix_data: Gf.Matrix4d = xformop.GetOpTransform(frame)
                    translate = matrix_data.ExtractTranslation()
                    translate *= scale_factor
                    matrix_data.SetTranslateOnly(translate)
                    xformop.Set(matrix_data, frame)

        if not (prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.BasisCurves)):  # type: ignore[call-overload]
            continue

        # don't recurse deeper than this
        it.PruneChildren()

        for attr_token in (UsdGeom.Tokens.points,):
            attr = prim.GetAttribute(attr_token)
            if not attr.IsValid():
                continue

            for frame in get_frames_from_attr(attr):
                data = np.array(attr.Get(frame))
                data *= scale_factor
                attr.Set(Vt.Vec3fArray.FromNumpy(data), frame)  # type: ignore[arg-type]

    UsdGeom.SetStageMetersPerUnit(
        stage, UsdGeom.GetStageMetersPerUnit(stage) / scale_factor
    )


TOPOLOGY_ATTRIBS = (
    UsdGeom.Tokens.cornerIndices,
    UsdGeom.Tokens.cornerSharpnesses,
    UsdGeom.Tokens.creaseIndices,
    UsdGeom.Tokens.creaseLengths,
    UsdGeom.Tokens.creaseSharpnesses,
    UsdGeom.Tokens.faceVaryingLinearInterpolation,
    UsdGeom.Tokens.faceVertexCounts,
    UsdGeom.Tokens.faceVertexIndices,
    UsdGeom.Tokens.holeIndices,
    UsdGeom.Tokens.interpolateBoundary,
    UsdGeom.Tokens.triangleSubdivisionRule,
)


def make_topo_attrs_default(stage: Usd.Stage) -> None:
    root_prim = stage.GetPseudoRoot()
    for prim in (it := iter(Usd.PrimRange(root_prim))):
        if not (prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.BasisCurves)):  # type: ignore[call-overload]
            continue

        # don't recurse deeper than this
        it.PruneChildren()

        for attr_token in TOPOLOGY_ATTRIBS:
            attr = prim.GetAttribute(attr_token)
            if not attr.IsValid():
                continue
            data = attr.Get(1)
            if data:
                attr.Clear()
                attr.Set(data, Usd.TimeCode.Default())


def update_material_bindings(
    stage: Usd.Stage, old: str, new: str, name_prepend: str = ""
) -> None:
    """Update material bindings to what Houdini will expect"""

    bindings = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(Sdf.Path(new)))
    for rel in bindings.GetCollectionBindingRels():
        t1, t2 = rel.GetTargets()
        # strip the namespace because the USD exporter strips the geo namespace but not the material namespace
        new_name_split = t2.name.split("_", 1)
        if len(new_name_split) < 2:
            continue
        new_name = new_name_split[1]
        # Change the material binding to match how it will look in Houdini
        rel.SetTargets(
            (
                t1,
                Sdf.Path(
                    f"{str(t2.GetParentPath()).replace(old, new)}/{name_prepend}{new_name}"
                ),
            )
        )


def move_prim(
    layer: Sdf.Layer, prim_to_move: Sdf.Path, new_prim_parent: Sdf.Path
) -> None:
    with Sdf.ChangeBlock():
        old_prim_parent = prim_to_move.GetParentPath()
        if old_prim_parent != new_prim_parent:
            prim_spec = Sdf.CreatePrimInLayer(layer, new_prim_parent)
            prim_spec.SetInfo(prim_spec.SpecifierKey, Sdf.SpecifierDef)

            edit = Sdf.BatchNamespaceEdit()
            edit.Add(Sdf.NamespaceEdit.Reparent(prim_to_move, new_prim_parent, -1))
            edit.Add(Sdf.NamespaceEdit.Remove(old_prim_parent.GetPrefixes()[0]))

            if not layer.Apply(edit):
                raise Exception("Failed to apply layer edit!")


def find_and_move_prim(
    layer: Sdf.Layer, prim_to_find: str, new_prim_parent: Sdf.Path
) -> None:
    """Searches for the prim with name `prim_to_find` and moves it underneath
    `new_prim_parent`. *Assumes only 1 prim with the given name*"""
    # TODO: will work in Usd v24?
    # editor = Usd.NamespaceEditor(self._stage)
    # editor.MovePrimAtPath(Sdf.Path("/WORLD/CAM/LnD_shotCam"), Sdf.Path("/"))
    # editor.ApplyEdits()

    prim_search: list[Sdf.Path] = []

    def traverse_kernel(path: Sdf.Path | str):
        if isinstance(path, str):
            path = Sdf.Path(path)
        if path.IsPrimPath():
            if path.name == prim_to_find:
                prim_search.append(path)

    layer.Traverse(Sdf.Path("/"), traverse_kernel)

    try:
        prim_to_move = prim_search.pop()
    except IndexError:
        raise RuntimeError(f"Could not find {prim_to_find} in export!")

    move_prim(layer, prim_to_move, new_prim_parent)


def remove_namespace(layer: Sdf.Layer, root: Sdf.Path = Sdf.Path("/")) -> bool:
    edit = Sdf.BatchNamespaceEdit()

    def traverse_kernel(path: Sdf.Path | str):
        if isinstance(path, str):
            path = Sdf.Path(path)
        if path.IsPrimPath():
            try:
                edit.Add(Sdf.NamespaceEdit.Rename(path, path.name.split("_", 1)[1]))
            except IndexError:
                print(f"Namespace not changed for {str(path)}")

    layer.Traverse(root, traverse_kernel)
    return layer.Apply(edit)


def split_by_namespace(stage: Usd.Stage, suffix: str) -> dict[str, Sdf.Layer]:
    root_layer = stage.GetRootLayer()
    root_layer_path = Path(root_layer.realPath)
    stage.SetEditTarget(root_layer)

    child_names = stage.GetPseudoRoot().GetChildrenNames()
    namespaces: set[str] = set()
    for n in child_names:
        namespace, item = n.split("_", 1)
        if item.startswith("S_"):  # static props
            remove_namespace(root_layer, Sdf.Path("/" + n))
            remove_namespace(root_layer, Sdf.Path("/" + item))
            s, namespace, _ = item.split("_", 2)
        namespaces.add(namespace)

    child_names = stage.GetPseudoRoot().GetChildrenNames()
    layers: dict[str, Sdf.Layer] = dict()
    for namespace in namespaces:
        layer_name = namespace.lower()
        layer_path = str(root_layer_path.parent / f"{layer_name}.{suffix}.usd")
        layer = create_or_clear_layer(layer_path)
        layer.TransferContent(root_layer)

        children_to_keep = [c for c in child_names if c.startswith(namespace + "_")]
        edit = Sdf.BatchNamespaceEdit()
        for child in child_names:
            if child not in children_to_keep:
                edit.Add(Sdf.NamespaceEdit.Remove("/" + child))

        layer.Apply(edit)
        if not remove_namespace(layer):
            raise RuntimeError(f"Could not remove namespace on layer `{layer_name}`")

        layer.Save()
        layers.update({layer_name: layer})

    # clear out root layer
    edit = Sdf.BatchNamespaceEdit()
    for child in child_names:
        edit.Add(Sdf.NamespaceEdit.Remove("/" + child))
    root_layer.Apply(edit)
    root_layer.Save()

    return layers


def float_range_compare_factory(
    keep_start: float | None, keep_end: float | None
) -> Callable[[float], bool]:
    def check_start(val: float) -> bool:
        return isclose(val, keep_start, rel_tol=1e-4) or (keep_start < val)  # type: ignore[arg-type, operator]

    def check_end(val: float) -> bool:
        return isclose(val, keep_end, rel_tol=1e-4) or (val < keep_end)  # type: ignore[arg-type, operator]

    def check_both(val: float) -> bool:
        return check_start(val) and check_end(val)

    if (keep_start is not None) and (keep_end is not None):
        return check_both
    elif keep_start is not None:
        return check_start
    elif keep_end is not None:
        return check_end
    else:
        raise ValueError("Must provide keep_start or keep_end")


def timesample_erase_kernel_factory(
    layer: Sdf.Layer, *, keep_start: float | None = None, keep_end: float | None = None
) -> Callable[[Sdf.Path | str], None]:
    """Returns a layer traversal kernel that erases time samples not between
    keep_start and keep_end
    NOTE: assume that all time samples are in this layer"""

    def kernel(path: Sdf.Path | str) -> None:
        if isinstance(path, str):
            path = Sdf.Path(path)
        if not path.IsPrimPropertyPath():
            return
        attr_spec = layer.GetAttributeAtPath(path)
        if not attr_spec.variability == Sdf.VariabilityVarying:
            return

        start = (
            layer.GetBracketingTimeSamplesForPath(path, keep_start)[0]
            if keep_start
            else None
        )
        end = (
            layer.GetBracketingTimeSamplesForPath(path, keep_end)[1]
            if keep_end
            else None
        )
        cmp = float_range_compare_factory(start, end)
        for ts in layer.ListTimeSamplesForPath(path):
            if cmp(ts):
                continue
            layer.EraseTimeSample(path, ts)

        if keep_start:
            layer.startTimeCode = keep_start
        if keep_end:
            layer.endTimeCode = keep_end

    return kernel


def split_preroll(
    anim_layer: Sdf.Layer, name: str, prim_path: Sdf.Path, tl: Timeline
) -> Sdf.Layer:
    """Split anim and preroll data into separate files, then stitch them together
    with Value Clips"""
    preroll_layer_path = str(Path(anim_layer.realPath).parent / f"{name}.preroll.usd")
    preroll_layer = create_or_clear_layer(preroll_layer_path)
    preroll_layer.TransferContent(anim_layer)

    preroll_layer.Traverse(
        preroll_layer.pseudoRoot.path,
        timesample_erase_kernel_factory(preroll_layer, keep_end=(tl.head - 1)),
    )
    preroll_layer.Save()

    anim_layer.Traverse(
        anim_layer.pseudoRoot.path,
        timesample_erase_kernel_factory(anim_layer, keep_start=tl.head),
    )
    anim_layer.Save()

    stitched_layer_path = str(Path(anim_layer.realPath).parent / f"{name}.usd")
    stiched_layer = create_or_clear_layer(stitched_layer_path)
    stiched_layer.TransferContent(anim_layer)

    timesample_files = [preroll_layer.realPath, anim_layer.realPath]
    topology_layer = create_or_clear_layer(
        UsdUtils.GenerateClipTopologyName(stiched_layer.resolvedPath)
    )
    manifest_layer = create_or_clear_layer(
        UsdUtils.GenerateClipManifestName(stiched_layer.realPath)
    )
    UsdUtils.StitchClipsTopology(topology_layer, timesample_files)
    UsdUtils.StitchClipsManifest(
        manifest_layer, topology_layer, timesample_files, prim_path
    )
    UsdUtils.StitchClips(
        stiched_layer, timesample_files, prim_path, tl.preroll, tl.end, False
    )
    stiched_layer.Save()

    return stiched_layer


def bind_materials_new_variant(
    stage: Usd.Stage, mat_path: Sdf.Path, new_path: Sdf.Path
) -> None:
    # Get the material prim and resolve the bound material
    mat_prim = stage.GetPrimAtPath(mat_path)
    if not mat_prim:
        raise RuntimeError(f"Material path {mat_path} does not exist.")

    bound_material = UsdShade.MaterialBindingAPI(mat_prim).ComputeBoundMaterial()[0]
    if not bound_material:
        raise RuntimeError(f"No material bound to {mat_path}")

    # Get the new prim to bind to
    new_prim = stage.OverridePrim(new_path)
    if not new_prim:
        raise RuntimeError(f"Target prim {new_path} does not exist.")

    # Bind the material to the new prim
    new_api = UsdShade.MaterialBindingAPI.Apply(new_prim)
    new_api.Bind(bound_material)

    stage.GetRootLayer().Save()


def add_variant_to_model(asset: Asset, variant: str) -> None:
    # Construct the full path to geo.usd
    if not asset.asset_path:
        raise ValueError("asset.asset_path is empty, cannot construct usd_path")

    usd_path = get_production_path() / asset.asset_path / "usd" / "geo.usd"

    # Open the USD stage
    stage = Usd.Stage.Open(str(usd_path))
    layer = stage.GetRootLayer()

    # Edit the usd so the new variant will be inside of it
    with Usd.EditContext(stage, layer):
        new_class_path = Sdf.Path(f"/__class__/character/{variant}")
        new_class_spec = Sdf.CreatePrimInLayer(layer, new_class_path)
        new_class_spec.SetInfo(new_class_spec.SpecifierKey, Sdf.SpecifierClass)

        original_geo_path = Sdf.Path(f"/character/{asset.name}")
        new_geo_path = Sdf.Path(f"/character/{variant}")

        Sdf.CopySpec(layer, original_geo_path, layer, new_geo_path)
        new_prim = stage.GetPrimAtPath(new_geo_path)

        # Set the inherits to the new path
        inherits = new_prim.GetInherits()
        inherits.ClearInherits()  # remove old ones
        inherits.AddInherit(new_class_path)  # add new one

    cfx_usd_path = get_production_path() / asset.asset_path / "usd" / "cfx.usd"

    cfx_stage = Usd.Stage.Open(str(cfx_usd_path))
    old_mat_bind_path: Sdf.Path = Sdf.Path(f"/character/{asset.name}/{asset.name}")
    new_mat_bind_path: Sdf.Path = Sdf.Path(f"/character/{variant}/{asset.name}")
    bind_materials_new_variant(cfx_stage, old_mat_bind_path, new_mat_bind_path)

    # Save the layer after editing
    layer.Save()


@attrs.define
class ChaserArgs:
    mode: ChaserMode = attrs.field(converter=int)
    timeline: Optional[Timeline] = attrs.field(
        default=None,
        kw_only=True,
        converter=lambda t: Timeline.from_json(t) if t else None,
    )
    props: Optional[dict[str, list[str]]] = attrs.field(
        default=None,
        kw_only=True,
        converter=lambda p: json.loads(p) if p else None,
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
        if self._chaser_args.mode == ChaserMode.ANIM:
            assert self._chaser_args.props is not None
            assert self._chaser_args.timeline is not None

            scale_down_geo(self._stage)
            make_topo_attrs_default(self._stage)
            layers = split_by_namespace(self._stage, "anim")

            root_layer = self._stage.GetRootLayer()
            root_layer_path = Path(root_layer.realPath)

            conn = DB.Get(DB_Config)

            for name, layer in layers.items():
                # takes of the end number if it's a copy in maya
                base_name = ""
                if name[-1].isdigit():
                    base_name = name[:-1]
                else:
                    base_name = name
                print(base_name)

                character_root_path = Sdf.Path("/ROOT/MODEL")

                stitched_layer = split_preroll(
                    layer, name, character_root_path, self._chaser_args.timeline
                )

                char_prim_spec: Sdf.PrimSpec
                char_prim_spec = Sdf.CreatePrimInLayer(
                    root_layer, Sdf.Path(f"__class__/character/{name}")
                )

                char_prim_spec.specifier = Sdf.SpecifierOver

                reference = Sdf.Reference(
                    f"./{Path(stitched_layer.realPath).relative_to(root_layer_path.parent)}",
                    character_root_path,
                )

                char_prim_spec.referenceList.appendedItems = [reference]

                asset = None
                relative_path_str = None
                try:
                    asset = conn.get_asset_by_name(base_name)

                    assert asset.asset_path
                    rig_path = (
                        str(asset.asset_path).replace("\\", "/") + "/usd/main.usd"
                    )
                    walk_up_len = (
                        len(root_layer_path.relative_to(get_production_path()).parts)
                        - 1
                    )

                    relative_path_str = "../" * walk_up_len + rig_path
                    relative_path = Sdf.Path(relative_path_str)
                    if str(relative_path) not in root_layer.subLayerPaths:  # type: ignore
                        root_layer.subLayerPaths.append(str(relative_path))
                    print(
                        f"[chaser] added rig sublayer for {name}: {relative_path_str}"
                    )
                except Exception:
                    print(f"[chaser] asset link failed for {name} (base={base_name})")
                    print(
                        f"    asset={getattr(asset, 'asset_path', None)} rig_path={rig_path if 'rig_path' in locals() else None}"
                    )
                    print(
                        f"    relative_path={relative_path_str} root_layer={root_layer.realPath}"
                    )
                    print(traceback.format_exc())
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
                    instance_prim_spec.referenceList.appendedItems = [
                        instance_reference
                    ]
                    instance_prim_spec.inheritPathList.prependedItems = [
                        Sdf.Path(f"/__class__/character/{name}")
                    ]

        elif self._chaser_args.mode == ChaserMode.CHAR:
            scale_down_geo(self._stage)
            update_material_bindings(self._stage, "/ROOT", "/ROOT/MODEL", "MAT_")

        elif self._chaser_args.mode == ChaserMode.CAM:
            # We don't scale down the camera here because we need to import it
            # back into Maya. Instead we'll scale it down when we import it into
            # Solaris.

            new_shotCam_path = Sdf.Path("/LnD_shotCam")
            find_and_move_prim(
                self._stage.GetEditTarget().GetLayer(), "world_CTRL", new_shotCam_path
            )
            self._stage.SetDefaultPrim(self._stage.GetPrimAtPath(new_shotCam_path))
        else:
            raise ValueError(
                f"{self._chaser_args.mode} is not a valid LnD chaser mode."
            )

        return True
