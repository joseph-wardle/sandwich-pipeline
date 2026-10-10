"""Composition fixture for `publish.geo_variants`: USD only, no Houdini scene.

Rebuilds the stage shapes the Python LOP inside the config node meets,
runs the real function with the edit target above the inputs (as Houdini
places it) and checks the composed result: bound materials and subsets per
variant, not just the reference list.

Run with Houdini's python so `pxr` and `pipe` resolve:

    .venv/bin/pipe houdini -p tests/test_geo_variants.py
"""

from __future__ import annotations

from pxr import Sdf, Usd, UsdGeom, UsdShade

from pipe.dcc.houdini.publish.geo_variants import confine_geo_variant_references

HEADER = '#usda 1.0\n(\n    defaultPrim = "ASSET"\n)\n'


def hidden(index: int, name: str, faces: int) -> str:
    """One Component Geometry Variants hidden prim as Houdini 21.0.596 writes it:
    geometry inside the prim's own `geo` variant, materials (a reference and the
    `mtl` variant set) outside it."""
    counts = ", ".join(["4"] * faces)
    idx = ", ".join(str(i) for i in range(faces))
    return f"""
over "ASSET_geo_variant_{index}" (
    hidden = true
)
{{
    def Xform "ASSET" (
        prepend references = </ASSET_geo_variant_{index}/ASSET_mtl_default>
        variants = {{
            string geo = "{name}"
            string mtl = "default"
        }}
        prepend variantSets = ["mtl", "geo"]
    )
    {{
        def Scope "mtl"
        {{
        }}
        variantSet "geo" = {{
            "{name}" {{
                def Scope "geo"
                {{
                    def Scope "render"
                    {{
                        def Mesh "geo"
                        {{
                            int[] faceVertexCounts = [{counts}]
                        }}
                    }}
                }}
            }}
        }}
        variantSet "mtl" = {{
            "default" {{
                over "geo"
                {{
                    over "render"
                    {{
                        over "geo" (
                            prepend apiSchemas = ["MaterialBindingAPI"]
                        )
                        {{
                            uniform token subsetFamily:materialBind:familyType = "nonOverlapping"
                            rel material:binding = </ASSET_geo_variant_{index}/ASSET/mtl/MAT_{name}>

                            def GeomSubset "MAT_{name}" (
                                prepend apiSchemas = ["MaterialBindingAPI"]
                            )
                            {{
                                uniform token elementType = "face"
                                uniform token familyName = "materialBind"
                                int[] indices = [{idx}]
                                rel material:binding = </ASSET_geo_variant_{index}/ASSET/mtl/MAT_{name}>
                            }}
                        }}
                    }}
                }}
            }}
        }}
    }}

    def "ASSET_mtl_default"
    {{
        def Scope "mtl"
        {{
            def Material "MAT_{name}"
            {{
            }}
        }}
    }}
}}
"""


MULTI_VARIANT = (
    HEADER
    + hidden(0, "A", 2)
    + hidden(1, "B", 1)
    + """
def Xform "ASSET" (
    prepend references = [
        </ASSET_geo_variant_1/ASSET>,
        </ASSET_geo_variant_0/ASSET>
    ]
    variants = {
        string geo = "A"
    }
    prepend variantSets = "geo"
)
{
    variantSet "geo" = {
        "__EMPTY" {
        }
        "A" (
            prepend references = </ASSET_geo_variant_0/ASSET>
        ) {
        }
        "B" (
            prepend references = </ASSET_geo_variant_1/ASSET>
        ) {
        }
    }
}
"""
)

SINGLE_VARIANT = (
    HEADER
    + """
def "ASSET_mtl_default"
{
    def Scope "mtl"
    {
        def Material "MAT_only"
        {
        }
    }
}

def Xform "ASSET" (
    prepend references = </ASSET_mtl_default>
    variants = {
        string geo = "__EMPTY"
    }
    prepend variantSets = "geo"
)
{
    def Scope "geo"
    {
        def Scope "render"
        {
            def Mesh "geo" (
                prepend apiSchemas = ["MaterialBindingAPI"]
            )
            {
                int[] faceVertexCounts = [4]
                rel material:binding = </ASSET/mtl/MAT_only>
            }
        }
    }
    variantSet "geo" = {
        "__EMPTY" {
        }
    }
}
"""
)

OLD_PUBLISH = (
    HEADER
    + """
over "ASSET_geo_variant_0" (
    hidden = true
)
{
    def Xform "ASSET"
    {
        def Scope "geo"
        {
            def Scope "render"
            {
                def Mesh "geo"
                {
                    int[] faceVertexCounts = [4]
                }
            }
        }
    }
}

def Xform "ASSET" (
    prepend references = </ASSET_geo_variant_0/ASSET>
    prepend variantSets = "geo"
)
{
    variantSet "geo" = {
        "__EMPTY" {
        }
    }
}
"""
)

ASSEMBLY = (
    HEADER
    + """
def Xform "ASSET" (
    prepend variantSets = "geo"
)
{
    def Xform "piece" (
        prepend references = @./child.usd@
    )
    {
    }
    variantSet "geo" = {
        "__EMPTY" {
        }
    }
}
"""
)


def open_stage(usda: str, edit_target: str) -> Usd.Stage:
    layer = Sdf.Layer.CreateAnonymous(".usda")
    assert layer.ImportFromString(usda)
    stage = Usd.Stage.Open(layer)
    if edit_target == "session":
        stage.SetEditTarget(stage.GetSessionLayer())
    return stage


def render_mesh(stage: Usd.Stage, variant: str | None):
    root = stage.GetPrimAtPath("/ASSET")
    if variant is not None:
        root.GetVariantSet("geo").SetVariantSelection(variant)
    mesh = stage.GetPrimAtPath("/ASSET/geo/render/geo")
    faces = len(UsdGeom.Mesh(mesh).GetFaceVertexCountsAttr().Get() or [])
    bound = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    subsets = {
        s.GetPrim().GetName(): list(s.GetIndicesAttr().Get() or [])
        for s in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(mesh))
    }
    return faces, (bound.GetPath().pathString if bound else None), subsets


def test_multi_variant_leak_is_removed() -> None:
    for edit_target in ("session", "root"):
        stage = open_stage(MULTI_VARIANT, edit_target)
        faces, _, subsets = render_mesh(stage, "B")
        assert faces == 1 and set(subsets) == {"MAT_A", "MAT_B"}, subsets
        assert max(subsets["MAT_A"]) >= faces, "fixture must reproduce the leak first"

        assert confine_geo_variant_references(stage) == 2

        for variant, other, faces_expected in (("A", "B", 2), ("B", "A", 1)):
            faces, bound, subsets = render_mesh(stage, variant)
            assert faces == faces_expected
            assert bound == f"/ASSET/mtl/MAT_{variant}", bound
            assert subsets == {f"MAT_{variant}": list(range(faces_expected))}, subsets
            assert not stage.GetPrimAtPath(
                f"/ASSET/mtl/MAT_{other}"
            ), "foreign material"
        root = stage.GetPrimAtPath("/ASSET")
        assert root.GetVariantSet("geo").GetVariantNames() == ["A", "B", "__EMPTY"]
        assert (
            confine_geo_variant_references(stage) == 0
        ), "second run must change nothing"


def test_single_variant_root_reference_is_kept() -> None:
    stage = open_stage(SINGLE_VARIANT, "session")
    assert confine_geo_variant_references(stage) == 0
    assert not stage.GetSessionLayer().GetPrimAtPath("/ASSET")
    assert render_mesh(stage, None)[1] == "/ASSET/mtl/MAT_only"


def test_old_publish_root_reference_is_kept() -> None:
    stage = open_stage(OLD_PUBLISH, "session")
    assert confine_geo_variant_references(stage) == 0
    assert render_mesh(stage, None)[0] == 1


def test_assembly_child_references_are_not_visited() -> None:
    stage = open_stage(ASSEMBLY, "session")
    assert confine_geo_variant_references(stage) == 0
    assert not stage.GetSessionLayer().GetPrimAtPath("/ASSET")


if __name__ == "__main__":
    for test in (
        test_multi_variant_leak_is_removed,
        test_single_variant_root_reference_is_kept,
        test_old_publish_root_reference_is_kept,
        test_assembly_child_references_are_not_visited,
    ):
        test()
        print("PASS", test.__name__)
