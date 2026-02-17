from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

# mypy: disable-error-code="union-attr"
import hou
import loptoolutils  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from typing import Optional


"""Node-graph builders for Houdini Solaris tools.

This module defines the canonical SKD component builder entry points used by
tool shelves and headless build scripts.
"""

SKD_LOOKDEV_TYPE = "skd::main::SKD_Lookdev::1.0"
SKD_MATLIB_TYPE = "skd::main::SKD_MatLib::1.0"
SKD_COMPONENT_OUTPUT_TYPE_CANDIDATES = (
    "skd.main::Lop/skd_component_output::1.0",
    "skd.main::skd_component_output::1.0",
)
SKD_COMPONENT_OUTPUT_TOKEN = "skd_component_output"
SKD_COMPONENT_GEOMETRY_NAME = "main"
SKD_BUILDER_MANAGED_KEY = "pipe_skd_builder_managed"
SKD_BUILDER_MANAGED_VALUE = "1"
SKD_BUILDER_NODE_NAME = "skd_component_output"

log = logging.getLogger(__name__)


def _latest_skd_type(default_type: str) -> str:
    """Return newest installed HDA matching default_type base, fallback to default."""
    base = default_type.rsplit("::", 1)[0]
    category = hou.lopNodeTypeCategory()
    candidates = [
        name for name in category.nodeTypes().keys() if name.startswith(base + "::")
    ]
    if not candidates:
        return default_type

    return max(candidates, key=_type_version_key)


def _type_version_key(type_name: str) -> tuple[int, ...]:
    """Sort Houdini type names by trailing version, if present."""
    _, _, version = type_name.rpartition("::")
    values: list[int] = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        values.append(int(digits))
    return tuple(values) if values else (0,)


def _resolve_component_output_type() -> str | None:
    """Return the preferred installed SKD Component Output node type."""
    installed = list(hou.lopNodeTypeCategory().nodeTypes().keys())

    for default_type in SKD_COMPONENT_OUTPUT_TYPE_CANDIDATES:
        base = default_type.rsplit("::", 1)[0]
        family = [name for name in installed if name.startswith(base + "::")]
        if family:
            return max(family, key=_type_version_key)

    matches = [name for name in installed if SKD_COMPONENT_OUTPUT_TOKEN in name.lower()]
    if matches:
        return max(matches, key=_type_version_key)
    return None


def create_skd_matlib(parent: hou.Node, node_name: str | None = None) -> hou.Node:
    node_type = _latest_skd_type(SKD_MATLIB_TYPE)
    if node_name:
        return parent.createNode(node_type, node_name)
    return parent.createNode(node_type)


def create_skd_lookdev(parent: hou.Node, node_name: str | None = None) -> hou.Node:
    node_type = _latest_skd_type(SKD_LOOKDEV_TYPE)
    if node_name:
        return parent.createNode(node_type, node_name)
    return parent.createNode(node_type)


def ensure_managed_skd_component_builder(parent: hou.Node | None = None) -> hou.Node:
    """Return exactly one managed SKD builder output, creating one if missing.

    This function is intentionally conservative:
    - It never deletes nodes.
    - It never rewires artist-authored networks.
    - It only creates a new builder when no managed/recognizable builder exists.
    """
    stage = _resolve_stage_context(parent)

    managed = _find_managed_builder_outputs(stage)
    if managed:
        if len(managed) > 1:
            for extra in managed[1:]:
                extra.setUserData(SKD_BUILDER_MANAGED_KEY, "0")
            log.warning(
                "Multiple managed SKD builders found in %s; using %s and unmarking extras",
                stage.path(),
                managed[0].path(),
            )
        return managed[0]

    existing = _find_existing_skd_builder_outputs(stage)
    if existing:
        adopted = existing[0]
        _mark_managed_builder(adopted)
        if len(existing) > 1:
            log.warning(
                "Multiple SKD-like builders found in %s; adopting %s",
                stage.path(),
                adopted.path(),
            )
        return adopted

    # No managed or recognizable builder exists; create one.
    output = create_skd_component_builder({}, parent=stage)
    _mark_managed_builder(output)
    return output


def _resolve_stage_context(parent: hou.Node | None) -> hou.Node:
    if parent is not None:
        return parent

    stage = hou.node("/stage")
    if stage is not None:
        return stage

    root = hou.node("/")
    if root is None:
        raise RuntimeError("Houdini root node '/' is unavailable")
    return root.createNode("lopnet", "stage")


def _create_component_output_node(
    *, kwargs: dict, parent: hou.Node | None
) -> hou.LopNode:
    node_type = _resolve_component_output_type()

    if parent is not None:
        if node_type:
            try:
                return parent.createNode(node_type, SKD_BUILDER_NODE_NAME)
            except hou.OperationFailed:
                log.warning(
                    "Failed to create SKD Component Output type %s; falling back to componentoutput",
                    node_type,
                    exc_info=True,
                )
        else:
            log.warning(
                "SKD Component Output HDA is not installed; falling back to componentoutput"
            )
        return parent.createNode("componentoutput", SKD_BUILDER_NODE_NAME)

    # Shelf tools may rely on genericTool kwargs insertion behavior.
    if node_type:
        try:
            out: hou.LopNode = loptoolutils.genericTool(kwargs, node_type)
            out.setName(SKD_BUILDER_NODE_NAME, unique_name=True)
            return out
        except hou.OperationFailed:
            log.warning(
                "Failed to create SKD Component Output type %s via shelf tool; falling back to componentoutput",
                node_type,
                exc_info=True,
            )
    else:
        log.warning(
            "SKD Component Output HDA is not installed; shelf tool is creating stock componentoutput"
        )

    out: hou.LopNode = loptoolutils.genericTool(kwargs, "componentoutput")
    out.setName(SKD_BUILDER_NODE_NAME, unique_name=True)
    return out


def _is_component_output_like(node: hou.Node) -> bool:
    node_type = node.type().name().lower()
    return node_type == "componentoutput" or SKD_COMPONENT_OUTPUT_TOKEN in node_type


def _is_skd_matlib_like(node: hou.Node) -> bool:
    node_type = node.type().name().lower()
    return "skd_matlib" in node_type or "lnd_matlib" in node_type


def _find_managed_builder_outputs(stage: hou.Node) -> list[hou.LopNode]:
    outputs: list[hou.LopNode] = []
    for node in stage.children():
        if not isinstance(node, hou.LopNode):
            continue
        if not _is_component_output_like(node):
            continue
        if node.userData(SKD_BUILDER_MANAGED_KEY) == SKD_BUILDER_MANAGED_VALUE:
            outputs.append(node)
    return outputs


def _find_existing_skd_builder_outputs(stage: hou.Node) -> list[hou.LopNode]:
    outputs: list[hou.LopNode] = []
    for node in stage.children():
        if not isinstance(node, hou.LopNode):
            continue
        if not _looks_like_skd_builder_output(node):
            continue
        outputs.append(node)
    return outputs


def _looks_like_skd_builder_output(node: hou.LopNode) -> bool:
    if not _is_component_output_like(node):
        return False

    inputs = node.inputs()
    if not inputs or inputs[0] is None:
        return False

    config = inputs[0]
    if "lnd_componentconfig" not in config.type().name().lower():
        return False

    config_inputs = config.inputs()
    if not config_inputs or config_inputs[0] is None:
        return False

    component_material = config_inputs[0]
    if component_material.type().name() != "componentmaterial":
        return False

    material_inputs = component_material.inputs()
    if len(material_inputs) < 2:
        return False
    if material_inputs[0] is None or material_inputs[1] is None:
        return False
    if material_inputs[0].type().name() != "componentgeometry":
        return False
    if not _is_skd_matlib_like(material_inputs[1]):
        return False
    return True


def _mark_managed_builder(output: hou.LopNode) -> None:
    output.setUserData(SKD_BUILDER_MANAGED_KEY, SKD_BUILDER_MANAGED_VALUE)


def lnd_clustersetup(kwargs: dict, parent: Optional[hou.Node] = None) -> hou.Node:
    out: hou.LopNode = loptoolutils.genericTool(kwargs, "componentoutput")
    out.setColor(hou.Color((0.616, 0.871, 0.769)))

    out_pos = out.position()

    # This is the context within the out node exists, which should be the stage context
    p = out.parent()

    # Fetches the other nodes
    ldv = p.createNode("sdm223::dev::LnD_Lookdev")
    prim = p.createNode("primitive")
    graft = p.createNode("graftstages")
    env = p.createNode("fetch")
    err = p.createNode("error")

    # Establishes Connections
    out.setInput(0, graft)
    graft.setInput(0, err)
    err.setInput(0, prim)
    ldv.setInput(0, out)
    out.setInput(1, env)

    # Arrange nodes in "Y" shape
    err_move = hou.Vector2(-1.22, 2.3)
    prim_move = hou.Vector2(-1.22, 3.5)
    graft_move = hou.Vector2(0.0, 1.0)
    ldv_move = hou.Vector2(0.0, -1.0)
    env_move = hou.Vector2(1.5, 0.5)
    prim.setPosition(prim_move + out_pos)
    err.setPosition(err_move + out_pos)
    graft.setPosition(graft_move + out_pos)
    ldv.setPosition(ldv_move + out_pos)
    env.setPosition(env_move + out_pos)

    # Configure environment fetch
    env.parm("loppath").set(f"../{ldv.name()}/OUT_ENV")

    # Configure Component Output node
    out.parm("mode").set(1)
    out.parm("doclassinherit").set(False)
    out.parm("lopoutput").set('$HIP/export/`chs("filename")`')
    graft.parm("destpath").set("/")
    prim.parm("primpath").set("$OS")
    out.parm("rootprim").set("`lopinputprim('.', 0)`")
    err.parm("errormsg1").set("Please name your primitive node")
    err.parm("severity1").set("error")

    error_expression = 'import re\nrgx = re.compile("primitive[0-9]+")\nreturn any(rgx.match(node.name()) for node in hou.pwd().inputAncestors())'
    err.parm("enable1").setExpression(
        error_expression, language=hou.exprLanguage.Python
    )

    # Set the Component Output as Selected
    out.setCurrent(True)
    out.setSelected(True, clear_all_selected=True)

    return out


def create_skd_component_geometry(
    kwargs: dict, parent: Optional[hou.Node] = None
) -> hou.Node:
    """Create the standard SKD Component Geometry node setup."""
    if parent:
        cgeo = parent.createNode("componentgeometry")
    else:
        cgeo = loptoolutils.genericTool(kwargs, "componentgeometry")

    # Rename to match publishing expectations.
    cgeo.setName(SKD_COMPONENT_GEOMETRY_NAME, unique_name=True)

    # Set up nodes inside of Component Geometry
    geo_sop = cgeo.node("./sopnet/geo")
    geo_sop.loadItemsFromFile(
        hou.hscriptStringExpression("$HSITE") + "/sop/component.cpio"
    )
    for name in ["default", "proxy", "simproxy"]:
        geo_sop.node(f"./{name}").setInput(0, geo_sop.node(f"./OUT_{name}"))

    # Configure Component Geometry node
    cgeo.parm("dogeommodelapi").set(True)
    cgeo.parm("attribs").set("P uv")
    cgeo.parm("indexattribs").set("texset")

    cgeo.setColor(hou.Color((0.616, 0.871, 0.769)))

    return cgeo


def create_skd_component_material(
    kwargs: dict, parent: Optional[hou.Node] = None
) -> hou.Node:
    """Create the standard SKD Component Material configuration."""
    MAT_ROOT = "/ASSET/mtl/MAT_"
    TS_PRIMVAR = "texset"

    if parent:
        cmat = parent.createNode("componentmaterial")
    else:
        cmat = loptoolutils.genericTool(kwargs, "componentmaterial")

    # Drive variant name directly from SKD_MatLib input 1.
    cmat.parm("variantname").setExpression(
        'chs(opinputpath(".",1)+"/mat_var")', hou.exprLanguage.Hscript
    )

    # set up primvar-based material assignment
    edit = cmat.node("./edit")
    assign = edit.createNode("assignmaterial")
    assign.setInput(0, edit.indirectInputs()[0])
    edit.node("./output0").setInput(0, assign)
    assign.parm("primpattern1").set(
        "%descendants(`lopinputprims('.', 0)`) & %type:Mesh"
    )
    assign.parm("matspecmethod1").set("vexpr")
    assign.parm("matspecvexpr1").set(
        f"return '{MAT_ROOT}' + usd_primvarelement(0, @primpath, '{TS_PRIMVAR}', usd_primvarindices(0, @primpath, '{TS_PRIMVAR}')[@elemnum]);"
    )
    assign.parm("geosubset1").set(True)

    cmat.setColor(hou.Color((0.616, 0.871, 0.769)))

    return cmat


def create_skd_component_builder(
    kwargs: dict, parent: Optional[hou.Node] = None
) -> hou.Node:
    """Build the standard SKD Solaris component network."""
    out = _create_component_output_node(kwargs=kwargs, parent=parent)
    out.setColor(hou.Color((0.616, 0.871, 0.769)))

    out_pos = out.position()
    p = parent or out.parent()
    geo = create_skd_component_geometry(kwargs, parent=p)
    mtl = create_skd_component_material(kwargs, parent=p)
    lib = create_skd_matlib(p, "matlib")
    cnf = p.createNode("sdm223::lnd_componentconfig")
    ldv = create_skd_lookdev(p, "lookdev")
    env = p.createNode("fetch", "env")
    out.setInput(0, cnf)
    out.setInput(1, env)
    cnf.setInput(0, mtl)
    mtl.setInput(0, geo)
    mtl.setInput(1, lib)
    ldv.setInput(0, out)

    # Arrange nodes in "Y" shape
    geo_move = hou.Vector2(-1.22, 3.5)
    mtl_move = hou.Vector2(0.0, 2.0)
    lib_move = hou.Vector2(1.22, 3.0)
    cnf_move = hou.Vector2(0.0, 1.0)
    ldv_move = hou.Vector2(0.0, -1.0)
    env_move = hou.Vector2(1.5, 0.5)
    geo.setPosition(geo_move + out_pos)
    mtl.setPosition(mtl_move + out_pos)
    lib.setPosition(lib_move + out_pos)
    cnf.setPosition(cnf_move + out_pos)
    ldv.setPosition(ldv_move + out_pos)
    env.setPosition(env_move + out_pos)

    # Configure environment fetch
    env.parm("loppath").set(f"../{ldv.name()}/OUT_ENV")

    # Configure Component Output node
    asset_name = Path(hou.hscriptStringExpression("$HIP")).name.strip() or "asset"
    out.parm("filename").set(f"{asset_name}.usd")
    out.parm("rootprim").set("/" + asset_name)
    out.parm("localize").set(False)
    out.parm("lopoutput").set('$HIP/publish/`chs("filename")`')
    out.parm("thumbnailmode").set(2)
    out.parm("renderer").set("RenderMan RIS")
    out.parm("thumbnailscenesource").set(1)
    out.parm("thumbnailinputcamera").set("/lookdev/cam")

    _mark_managed_builder(out)

    # Set Geometry as last selected
    geo.setSelected(True, clear_all_selected=True)

    return out


def _hide_contextoptions_folders(node: hou.Node) -> None:
    ptg = node.parmTemplateGroup()
    for f in ("Basic Options", "Time Based Options", "Pattern Matching Options"):
        ptg.hideFolder(f, True)
    node.setParmTemplateGroup(ptg)


def bobo_layoutgroup(kwargs: dict) -> hou.Node:
    contextoptions: hou.LopNode = loptoolutils.genericTool(kwargs, "editcontextoptions")

    pos = contextoptions.position()
    p = contextoptions.parent()
    beginblock = p.createNode("begincontextoptionsblock")
    groupprim = p.createNode("primitive")

    if old_inputs := contextoptions.inputs():
        beginblock.setInput(0, old_inputs[0])
    contextoptions.setInput(0, groupprim)
    contextoptions.parm("createoptionsblock").set(True)
    groupprim.setInput(0, beginblock)

    for n in (beginblock, groupprim, contextoptions):
        n.setColor(hou.Color(0.565, 0.494, 0.863))

    groupprim.setUserData("nodeshape", "chevron_down")
    contextoptions.setUserData("nodeshape", "chevron_up")

    beginblock.setName("beginlayoutgroup", True)
    groupprim.setName("layoutprim", True)
    contextoptions.setName("layoutgroup", True)

    groupprim.parm("primpath").set("`@PATH`")
    groupprim.parm("primkind").set("Group")
    groupprim.parm("parentprimtype").set("Scope")

    contextoptions.addSpareParmTuple(
        hou.StringParmTemplate(
            name="group", label="Group Name", num_components=1, default_value=("$OS",)
        )
    )
    contextoptions.parm("optioncount").insertMultiParmInstance(0)
    contextoptions.parm("optionname1").set("GROUP")
    contextoptions.parm("optionstrvalue1").set('`chs("./group")`')
    contextoptions.parm("optionname2").set("PATH")
    contextoptions.parm("optionstrvalue2").set(
        '/environment/`@ASSEMBLY`/`chs("./group")`'
    )

    contextoptions.parm("createoptionsblock").hide(True)
    _hide_contextoptions_folders(contextoptions)

    beginblock_move = hou.Vector2(0, 2.0)
    groupprim_move = hou.Vector2(0, 1.5)
    beginblock.setPosition(beginblock_move + pos)
    groupprim.setPosition(groupprim_move + pos)

    return contextoptions


def bobo_layout(kwargs: dict) -> hou.Node:
    contextoptions: hou.Node = loptoolutils.genericTool(kwargs, "editcontextoptions")

    pos = contextoptions.position()
    p = contextoptions.parent()
    envprim = p.createNode("primitive")
    layoutprim = p.createNode("primitive")
    merge = p.createNode("merge")
    rop = p.createNode("usd_rop")
    load = p.createNode("loadlayer")
    edit = p.createNode("dbclark::bobo_edit_properties")

    contextoptions.setInput(0, merge)
    merge.setInput(0, layoutprim)
    layoutprim.setInput(0, envprim)
    rop.setInput(0, contextoptions)
    merge.setInput(1, edit)
    edit.setInput(0, load)

    contextoptions.setName("layout_name", True)
    envprim.setName("environment_xform", True)
    layoutprim.setName("assembly_prim", True)
    rop.setName("PUBLISH", True)
    load.setName("Maya_Import", True)

    for n in (contextoptions, envprim, layoutprim, merge, rop, load, edit):
        n.setColor(hou.Color(0.188, 0.529, 0.45))

    envprim.setUserData("nodeshape", "chevron_down")
    layoutprim.setUserData("nodeshape", "chevron_down")
    contextoptions.setUserData("nodeshape", "chevron_up")

    envprim.parm("primpath").set("/environment")
    envprim.parm("parentprimtype").set("None")
    envprim.parm("primtype").set("UsdGeomXform")

    layoutprim.parm("primpath").set("`@PATH`")
    layoutprim.parm("primkind").set("Assembly")
    layoutprim.parm("parentprimtype").set("UsdGeomXform")

    contextoptions.addSpareParmTuple(
        hou.StringParmTemplate(
            name="assembly",
            label="Assembly Name",
            num_components=1,
            default_value=("$OS",),
        )
    )
    contextoptions.parm("optioncount").insertMultiParmInstance(0)
    contextoptions.parm("optionname1").set("ASSEMBLY")
    contextoptions.parm("optionstrvalue1").set('`chs("./assembly")`')
    contextoptions.parm("optionname2").set("PATH")
    contextoptions.parm("optionstrvalue2").set('/environment/`chs("./assembly")`')

    contextoptions.parm("createoptionsblock").hide(True)
    _hide_contextoptions_folders(contextoptions)

    rop.parm("lopoutput").set("$HIP/main.usd")

    load.parm("filepath").set("$HIP/maya_layout.usd")

    envprim_move = hou.Vector2(0, 6.7)
    layoutprim_move = hou.Vector2(0, 6.0)
    merge_move = hou.Vector2(0, 1.0)
    rop_move = hou.Vector2(0, -2.0)
    load_move = hou.Vector2(3, 1)
    edit_move = hou.Vector2(3, 0)
    envprim.setPosition(envprim_move + pos)
    layoutprim.setPosition(layoutprim_move + pos)
    merge.setPosition(merge_move + pos)
    rop.setPosition(rop_move + pos)
    load.setPosition(load_move + pos)
    edit.setPosition(edit_move + pos)

    return contextoptions
