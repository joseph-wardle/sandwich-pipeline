from __future__ import annotations

from math import isclose
from pathlib import Path
from typing import TYPE_CHECKING

import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]
import numpy as np
from maya.api.OpenMaya import MDagPath, MFnDependencyNode
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Mapping, Protocol

    class TimeSampleble(Protocol):
        def GetTimeSamples(self) -> list[float]: ...

        def GetNumTimeSamples(self) -> int: ...


from shared.util import get_production_path

from pipe.shotgrid import Asset
from pipe.struct.timeline import Timeline


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

        if not (prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.BasisCurves)):  # type: ignore
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
        if not (prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.BasisCurves)):  # type: ignore
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


def prefix_material_bindings(
    stage: Usd.Stage,
    collection_owner_path: Sdf.Path,
    name_prepend: str = "",
) -> None:
    """
    Convert collection material bindings to use paths relative to the geo prim and optionally add a prefix.
    """
    geo_prim = stage.GetPrimAtPath(collection_owner_path)
    bindings = UsdShade.MaterialBindingAPI(geo_prim)

    for rel in bindings.GetCollectionBindingRels():
        collection_target, material_target = rel.GetTargets()
        # Optionally rename material
        if name_prepend:
            parent = material_target.GetParentPath()
            material_target = parent.AppendChild(
                f"{name_prepend}{material_target.name}"
            )
        rel.SetTargets((collection_target, material_target))


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


def path_to_maya_dag_map(
    dag_to_usd: mayaUsdLib.DagToUsdMap,
) -> dict[Sdf.Path, MDagPath]:
    """Build a mapping from USD prim -> original Maya MDagPath"""
    prim_namespace_map: dict[Sdf.Path, MDagPath] = {}
    for mapping in dag_to_usd:
        dag_path: MDagPath = mapping.key()
        prim: Sdf.Path = mapping.data()

        prim_namespace_map[prim] = dag_path
    return prim_namespace_map


def remove_namespace(
    layer: Sdf.Layer,
    path_dag_map: Mapping[Sdf.Path, MDagPath],
    root: Sdf.Path = Sdf.Path("/"),
) -> bool:
    edit = Sdf.BatchNamespaceEdit()

    def traverse_kernel(path: Sdf.Path | str):
        if isinstance(path, str):
            path = Sdf.Path(path)

        if not path.IsPrimPath():  # We only need to modify prims (not properties)
            return
        dag_path = path_dag_map.get(path)
        if dag_path:
            # By default the Maya export gives the USD meshes the names of their maya transform counterparts.
            node = MFnDependencyNode(dag_path.transform())
            # Still have to manually strip the namespace, but at least it's with a colon which maya ONLY allows for use as a namespace delimiter.
            name = node.name().rsplit(":", 1)[-1]
            edit.Add(Sdf.NamespaceEdit.Rename(path, name))
        else:
            print(f"No Maya mapping found for USD path: {path}. Namespace not removed.")

    layer.Traverse(root, traverse_kernel)
    return layer.Apply(edit)


def split_by_namespace(
    stage: Usd.Stage, suffix: str, path_dag_map: Mapping[Sdf.Path, MDagPath]
) -> dict[str, Sdf.Layer]:
    root_layer = stage.GetRootLayer()
    root_layer_path = Path(root_layer.realPath)
    stage.SetEditTarget(root_layer)

    root_level_prims = stage.GetPseudoRoot().GetChildren()
    namespaces: set[str] = set()
    namespace_prims: dict[str, set[Usd.Prim]] = {}
    prim: Usd.Prim
    for prim in root_level_prims:
        dag_path = path_dag_map[prim.GetPath()]
        node = MFnDependencyNode(dag_path.node())
        namespace = node.namespace
        namespaces.add(namespace)
        if namespace not in namespace_prims:
            namespace_prims[namespace] = {prim}
        else:
            namespace_prims[namespace].add(prim)

    layers: dict[str, Sdf.Layer] = dict()
    for namespace in namespaces:  # Create a layer for each rig (namespace)
        layer_name = namespace.lower()
        layer_path = str(root_layer_path.parent / f"{layer_name}.{suffix}.usd")
        layer = create_or_clear_layer(layer_path)
        layer.TransferContent(root_layer)

        # Get the prims belonging to this namespace, discard the rest for this layer.
        prims_to_keep = [
            prim for prim in root_level_prims if prim in namespace_prims[namespace]
        ]
        edit = Sdf.BatchNamespaceEdit()
        for prim in root_level_prims:
            if prim not in prims_to_keep:
                edit.Add(Sdf.NamespaceEdit.Remove(prim.GetPath()))

        layer.Apply(edit)
        if not remove_namespace(layer, path_dag_map):
            raise RuntimeError(f"Could not remove namespace on layer `{layer_name}`")

        layer.Save()
        layers.update({layer_name: layer})

    # clear out root layer
    edit = Sdf.BatchNamespaceEdit()
    for prim in root_level_prims:
        edit.Add(Sdf.NamespaceEdit.Remove(prim.GetPath()))
    root_layer.Apply(edit)
    root_layer.Save()

    return layers


def float_range_compare_factory(
    keep_start: float | None, keep_end: float | None
) -> Callable[[float], bool]:
    def check_start(val: float) -> bool:
        return isclose(val, keep_start, rel_tol=1e-4) or (keep_start < val)  # type: ignore

    def check_end(val: float) -> bool:
        return isclose(val, keep_end, rel_tol=1e-4) or (val < keep_end)  # type: ignore

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
