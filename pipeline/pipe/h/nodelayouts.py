from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, cast

# mypy: disable-error-code="union-attr"
import hou
import loptoolutils  # type: ignore[import-not-found]

from . import variants

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
SKD_VARIANT_GRAPH_MANAGED_KEY = "pipe_skd_variant_graph_managed"
SKD_VARIANT_GRAPH_MANAGED_VALUE = "1"
SKD_VARIANT_GRAPH_OWNER_KEY = "pipe_skd_variant_graph_owner"
SKD_VARIANT_WARNINGS_KEY = "pipe_skd_variant_graph_warnings"
SKD_VARIANT_COMMENT_PREFIX = "SKD Variant Graph Warnings"
SKD_PENDING_COMMENT_PREFIX = "Pending Variant:"
SKD_VARIANT_BOX_PREFIX = "skd_variant_"

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
        node = parent.createNode(node_type)
        node.setName(node_name, unique_name=True)
        return node
    return parent.createNode(node_type)


def create_skd_lookdev(parent: hou.Node, node_name: str | None = None) -> hou.Node:
    node_type = _latest_skd_type(SKD_LOOKDEV_TYPE)
    if node_name:
        node = parent.createNode(node_type)
        node.setName(node_name, unique_name=True)
        return node
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


def _create_component_output_node(*, kwargs: dict, parent: hou.Node | None) -> hou.Node:
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
            created_node = cast(hou.Node, loptoolutils.genericTool(kwargs, node_type))
            created_node.setName(SKD_BUILDER_NODE_NAME, unique_name=True)
            return created_node
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

    fallback_node = cast(hou.Node, loptoolutils.genericTool(kwargs, "componentoutput"))
    fallback_node.setName(SKD_BUILDER_NODE_NAME, unique_name=True)
    return fallback_node


def _is_component_output_like(node: hou.Node) -> bool:
    node_type = node.type().name().lower()
    return node_type == "componentoutput" or SKD_COMPONENT_OUTPUT_TOKEN in node_type


def _is_skd_matlib_like(node: hou.Node) -> bool:
    node_type = node.type().name().lower()
    return "skd_matlib" in node_type or "lnd_matlib" in node_type


def _find_managed_builder_outputs(stage: hou.Node) -> list[hou.Node]:
    outputs: list[hou.Node] = []
    for node in stage.children():
        if not _is_component_output_like(node):
            continue
        if node.userData(SKD_BUILDER_MANAGED_KEY) == SKD_BUILDER_MANAGED_VALUE:
            outputs.append(node)
    return outputs


def _find_existing_skd_builder_outputs(stage: hou.Node) -> list[hou.Node]:
    outputs: list[hou.Node] = []
    for node in stage.children():
        if not _looks_like_skd_builder_output(node):
            continue
        outputs.append(node)
    return outputs


def _looks_like_skd_builder_output(node: hou.Node) -> bool:
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

    payload = config_inputs[0]
    payload_type = payload.type().name()

    # Phase 4 variant graph may feed componentgeometryvariants directly.
    if payload_type == "componentgeometryvariants":
        return True

    if payload_type != "componentmaterial":
        return False

    material_inputs = payload.inputs()
    if len(material_inputs) < 2:
        return False
    if material_inputs[0] is None or material_inputs[1] is None:
        return False
    if material_inputs[0].type().name() not in {
        "componentgeometry",
        "componentmaterial",
    }:
        return False
    if not _is_skd_matlib_like(material_inputs[1]):
        return False
    return True


def _mark_managed_builder(output: hou.Node) -> None:
    output.setUserData(SKD_BUILDER_MANAGED_KEY, SKD_BUILDER_MANAGED_VALUE)


def _set_parm_if_exists(node: hou.Node, parm_name: str, value) -> None:
    parm = node.parm(parm_name)
    if parm is None:
        return
    parm.set(value)


def _mark_managed_variant_node(node: hou.Node, *, owner_path: str) -> None:
    node.setUserData(SKD_VARIANT_GRAPH_MANAGED_KEY, SKD_VARIANT_GRAPH_MANAGED_VALUE)
    node.setUserData(SKD_VARIANT_GRAPH_OWNER_KEY, owner_path)


def _clear_managed_variant_nodes(
    parent: hou.Node, *, keep_paths: set[str], owner_path: str
) -> None:
    for node in list(parent.children()):
        if node.path() in keep_paths:
            continue
        if (
            node.userData(SKD_VARIANT_GRAPH_MANAGED_KEY)
            != SKD_VARIANT_GRAPH_MANAGED_VALUE
        ):
            continue
        if node.userData(SKD_VARIANT_GRAPH_OWNER_KEY) not in ("", owner_path):
            continue
        node.destroy()


def _clear_managed_variant_boxes(parent: hou.Node, *, owner_path: str) -> None:
    if not hasattr(parent, "networkBoxes"):
        return
    for net_box in parent.networkBoxes():
        net_box_any = cast(Any, net_box)
        box_name = ""
        try:
            box_name = net_box_any.name()
        except Exception:
            box_name = ""

        try:
            managed = (
                net_box_any.userData(SKD_VARIANT_GRAPH_MANAGED_KEY)
                == SKD_VARIANT_GRAPH_MANAGED_VALUE
            )
            owner = net_box_any.userData(SKD_VARIANT_GRAPH_OWNER_KEY)
        except Exception:
            managed = False
            owner = ""

        if not managed and not box_name.startswith(SKD_VARIANT_BOX_PREFIX):
            continue
        if managed and owner not in ("", owner_path):
            continue
        try:
            net_box_any.destroy()
        except Exception:
            continue


def _create_managed_variant_box(
    parent: hou.Node,
    *,
    owner_path: str,
    name: str,
    label: str,
    nodes: list[hou.Node],
) -> None:
    if not hasattr(parent, "createNetworkBox"):
        return
    try:
        net_box = parent.createNetworkBox()
        net_box.setName(f"{SKD_VARIANT_BOX_PREFIX}{name}", unique_name=True)
    except Exception:
        return

    net_box_any = cast(Any, net_box)
    try:
        net_box_any.setUserData(
            SKD_VARIANT_GRAPH_MANAGED_KEY, SKD_VARIANT_GRAPH_MANAGED_VALUE
        )
        net_box_any.setUserData(SKD_VARIANT_GRAPH_OWNER_KEY, owner_path)
    except Exception:
        pass

    if hasattr(net_box, "setLabel"):
        try:
            net_box_any.setLabel(label)
        except Exception:
            pass
    if hasattr(net_box, "setComment"):
        try:
            net_box.setComment(label)
            if hasattr(net_box, "setGenericFlag"):
                try:
                    network_item_flag = getattr(hou, "networkItemFlag", None)
                    display_comment_flag = getattr(
                        network_item_flag, "DisplayComment", None
                    )
                    if display_comment_flag is not None:
                        net_box_any.setGenericFlag(display_comment_flag, True)
                except Exception:
                    pass
        except Exception:
            pass

    for item in nodes:
        try:
            net_box.addItem(item)
        except Exception:
            try:
                net_box.addNode(item)
            except Exception:
                continue
    try:
        net_box.fitAroundContents()
    except Exception:
        try:
            points = [node.position() for node in nodes]
            if not points:
                return
            min_x = min(point.x() for point in points) - 1.6
            max_x = max(point.x() for point in points) + 3.1
            min_y = min(point.y() for point in points) - 1.2
            max_y = max(point.y() for point in points) + 1.8
            net_box.setPosition(hou.Vector2(min_x, max_y))
            net_box_any.setSize((max_x - min_x, max_y - min_y))
        except Exception:
            pass


def _set_variant_generation_warnings(node: hou.Node, warnings: list[str]) -> None:
    node.setUserData(SKD_VARIANT_WARNINGS_KEY, json.dumps(warnings))

    summary = node.parm("status_summary")
    payload = node.parm("status_json")
    if warnings:
        preview = "; ".join(warnings[:3])
        if len(warnings) > 3:
            preview = f"{preview}; +{len(warnings) - 3} more"
        node.setComment(f"{SKD_VARIANT_COMMENT_PREFIX} ({len(warnings)}): {preview}")
        if summary is not None:
            summary.set(
                f"Variant graph generated with {len(warnings)} warning(s). "
                "See node comment or user data for details."
            )
    else:
        if node.comment().startswith(SKD_VARIANT_COMMENT_PREFIX):
            node.setComment("")
        if summary is not None:
            summary.set("Ready")

    if payload is not None:
        payload.set(
            json.dumps(
                {
                    "status": "success",
                    "warnings": [
                        {"code": "VariantGraphWarning", "message": w} for w in warnings
                    ],
                    "errors": [],
                },
                indent=2,
            )
        )


def lnd_clustersetup(kwargs: dict, parent: Optional[hou.Node] = None) -> hou.Node:
    out = cast(hou.Node, loptoolutils.genericTool(kwargs, "componentoutput"))
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
    cast(Any, env.parm("loppath")).set(f"../{ldv.name()}/OUT_ENV")

    # Configure Component Output node
    cast(Any, out.parm("mode")).set(1)
    cast(Any, out.parm("doclassinherit")).set(False)
    cast(Any, out.parm("lopoutput")).set('$HIP/export/`chs("filename")`')
    cast(Any, graft.parm("destpath")).set("/")
    cast(Any, prim.parm("primpath")).set("$OS")
    cast(Any, out.parm("rootprim")).set("`lopinputprim('.', 0)`")
    cast(Any, err.parm("errormsg1")).set("Please name your primitive node")
    cast(Any, err.parm("severity1")).set("error")

    error_expression = 'import re\nrgx = re.compile("primitive[0-9]+")\nreturn any(rgx.match(node.name()) for node in hou.pwd().inputAncestors())'
    cast(Any, err.parm("enable1")).setExpression(
        error_expression, language=hou.exprLanguage.Python
    )

    # Set the Component Output as Selected
    out.setCurrent(True)
    out.setSelected(True, clear_all_selected=True)

    return out


def create_skd_component_geometry(
    kwargs: dict,
    parent: Optional[hou.Node] = None,
    *,
    node_name: str | None = None,
    geo_variant: str | None = None,
    source_expression: str | None = None,
) -> hou.Node:
    """Create the standard SKD Component Geometry node setup."""
    if parent:
        cgeo = parent.createNode("componentgeometry")
    else:
        cgeo = loptoolutils.genericTool(kwargs, "componentgeometry")

    # Rename to match publishing expectations.
    cgeo.setName(node_name or SKD_COMPONENT_GEOMETRY_NAME, unique_name=True)

    # Set up nodes inside of Component Geometry
    geo_sop = cgeo.node("./sopnet/geo")
    if geo_sop is not None:
        geo_sop.loadItemsFromFile(
            hou.hscriptStringExpression("$HSITE") + "/sop/component.cpio"
        )
        for name in ["default", "proxy", "simproxy"]:
            target = geo_sop.node(f"./{name}")
            source = geo_sop.node(f"./OUT_{name}")
            if target is not None and source is not None:
                target.setInput(0, source)
    else:
        log.warning("Component Geometry node is missing /sopnet/geo: %s", cgeo.path())

    # Configure Component Geometry node
    _set_parm_if_exists(cgeo, "dogeommodelapi", True)
    _set_parm_if_exists(cgeo, "attribs", "P uv")
    _set_parm_if_exists(cgeo, "indexattribs", "texset")
    _set_parm_if_exists(cgeo, "geovariantname", geo_variant or cgeo.name())

    importer = cgeo.node("./sopnet/geo/import_usd")
    if importer is not None:
        _set_parm_if_exists(
            importer,
            "filepath1",
            source_expression or variants.default_geo_source_expression(),
        )
    else:
        log.warning("Component Geometry SOP is missing import_usd: %s", cgeo.path())

    cgeo.setColor(hou.Color((0.616, 0.871, 0.769)))

    return cgeo


def create_skd_component_material(
    kwargs: dict,
    parent: Optional[hou.Node] = None,
    *,
    node_name: str | None = None,
    geo_variant_name: str | None = None,
    variant_name: str | None = None,
    use_input_variant_expression: bool = True,
) -> hou.Node:
    """Create the standard SKD Component Material configuration."""
    TS_PRIMVAR = "texset"

    if parent:
        cmat = parent.createNode("componentmaterial")
    else:
        cmat = loptoolutils.genericTool(kwargs, "componentmaterial")

    if node_name:
        cmat.setName(node_name, unique_name=True)

    # Drive variant name from SKD_MatLib input by default, but allow explicit
    # names for managed variant graph generation.
    variant_parm = cmat.parm("variantname")
    if variant_parm is not None:
        if use_input_variant_expression:
            variant_parm.setExpression(
                'chs(opinputpath(".",1)+"/mat_var")', hou.exprLanguage.Hscript
            )
        elif variant_name is not None:
            variant_parm.set(variant_name)
    _set_parm_if_exists(cmat, "variantset", "mtl")

    # set up primvar-based material assignment
    edit = cmat.node("./edit")
    if edit is None:
        log.warning("Component Material node is missing ./edit: %s", cmat.path())
    else:
        assign = edit.createNode("assignmaterial")
        indirect_inputs = edit.indirectInputs()
        if indirect_inputs:
            assign.setInput(0, indirect_inputs[0])
        output = edit.node("./output0")
        if output is not None:
            output.setInput(0, assign)

        if variant_name is not None:
            mat_root = f"{variants.material_scope_path(variant_name, geo_variant=geo_variant_name)}MAT_"
        elif use_input_variant_expression:
            mat_root = (
                '/ASSET/mtl/g_`chs(opinputpath(".",1)+"/geo_var")`/'
                'v_`chs(opinputpath(".",1)+"/mat_var")`/MAT_'
            )
        else:
            mat_root = f"{variants.material_scope_path(variants.DEFAULT_MAT_VARIANT, geo_variant=variants.DEFAULT_GEO_VARIANT)}MAT_"

        _set_parm_if_exists(
            assign, "primpattern1", "%descendants(`lopinputprims('.', 0)`) & %type:Mesh"
        )
        _set_parm_if_exists(assign, "matspecmethod1", "vexpr")
        _set_parm_if_exists(
            assign,
            "matspecvexpr1",
            (
                f"return '{mat_root}' + usd_primvarelement(0, @primpath, '{TS_PRIMVAR}', "
                f"usd_primvarindices(0, @primpath, '{TS_PRIMVAR}')[@elemnum]);"
            ),
        )
        _set_parm_if_exists(assign, "geosubset1", True)

    cmat.setColor(hou.Color((0.616, 0.871, 0.769)))

    return cmat


def _configure_component_output_defaults(out: hou.Node) -> None:
    asset_name = Path(hou.hscriptStringExpression("$HIP")).name.strip() or "asset"
    _set_parm_if_exists(out, "filename", f"{asset_name}.usd")
    _set_parm_if_exists(out, "rootprim", "/" + asset_name)
    _set_parm_if_exists(out, "localize", False)
    _set_parm_if_exists(out, "lopoutput", '$HIP/publish/`chs("filename")`')
    _set_parm_if_exists(out, "thumbnailmode", 2)
    _set_parm_if_exists(out, "renderer", "RenderMan RIS")
    _set_parm_if_exists(out, "thumbnailscenesource", 1)
    _set_parm_if_exists(out, "thumbnailinputcamera", "/lookdev/cam")


def _set_matlib_variant_selection(
    matlib: hou.Node, *, geo_variant: str, mat_variant: str
) -> None:
    material_prefix = variants.material_scope_path(
        mat_variant,
        geo_variant=geo_variant,
    )
    _set_parm_if_exists(matlib, "geo_var", geo_variant)
    _set_parm_if_exists(matlib, "mat_var", mat_variant)
    _set_parm_if_exists(matlib, "matpathprefix", material_prefix)
    _set_parm_if_exists(matlib, "materialpathprefix", material_prefix)
    _set_parm_if_exists(matlib, "matnodepattern", "MAT_*")


def _discover_asset_variants_from_shotgrid() -> (
    tuple[tuple[str, ...], tuple[str, ...], list[str]]
):
    """Return declared (geo, mat) variants from ShotGrid for the current ASSET."""
    warnings: list[str] = []

    try:
        asset_name = str(hou.contextOption("ASSET")).strip()
    except Exception:
        asset_name = ""
    if not asset_name:
        warnings.append(
            "ASSET context option is not set; variant graph generation fell back to filesystem-only discovery."
        )
        return (), (), warnings

    try:
        from env_sg import DB_Config

        from pipe.db import DB
    except Exception as exc:
        warnings.append(
            f"ShotGrid metadata unavailable for ASSET '{asset_name}': {exc}. Using filesystem-only variant discovery."
        )
        return (), (), warnings

    try:
        connection = DB.Get(DB_Config)
        asset = connection.get_asset_by_name(asset_name)
    except Exception as exc:
        warnings.append(
            f"Failed to query ShotGrid variants for ASSET '{asset_name}': {exc}. Using filesystem-only variant discovery."
        )
        return (), (), warnings

    geo = tuple(
        sorted(
            {
                value.strip()
                for value in getattr(asset, "geometry_variants", ())
                if value and value.strip()
            },
            key=str.casefold,
        )
    )
    mat = tuple(
        sorted(
            {
                value.strip()
                for value in getattr(asset, "material_variants", ())
                if value and value.strip()
            },
            key=str.casefold,
        )
    )

    if not geo:
        warnings.append(
            f"ShotGrid returned no geometry variants for ASSET '{asset_name}'; geometry variants will be inferred from publish files."
        )
    if not mat:
        warnings.append(
            f"ShotGrid returned no material variants for ASSET '{asset_name}'; material variants will be inferred from publish files."
        )
    return geo, mat, warnings


def _rebuild_matlib_for_variant(
    matlib: hou.Node,
    *,
    geo_variant: str,
    mat_variant: str,
    warnings: list[str],
) -> None:
    if not isinstance(matlib, hou.LopNode):
        return
    try:
        from . import shading as shading_module
    except Exception as exc:
        warnings.append(f"MatLib rebuild unavailable for {matlib.path()}: {exc}")
        return

    try:
        shading_module.matlib_rebuild(matlib)
    except Exception as exc:
        warnings.append(
            f"MatLib rebuild failed for geo='{geo_variant}' mat='{mat_variant}': {exc}"
        )


def _first_managed_geometry_node(
    parent: hou.Node, *, owner_path: str | None = None
) -> hou.Node | None:
    geometry_nodes = [
        node
        for node in parent.children()
        if node.type().name() == "componentgeometry"
        and node.userData(SKD_VARIANT_GRAPH_MANAGED_KEY)
        == SKD_VARIANT_GRAPH_MANAGED_VALUE
        and (
            owner_path is None
            or node.userData(SKD_VARIANT_GRAPH_OWNER_KEY) == owner_path
        )
    ]
    if not geometry_nodes:
        return None
    return sorted(geometry_nodes, key=lambda node: node.name().casefold())[0]


def _set_node_bypass(node: hou.Node, enabled: bool) -> None:
    try:
        node.bypass(enabled)  # ty:ignore[unresolved-attribute]
        return
    except Exception:
        pass
    try:
        node.setGenericFlag(hou.nodeFlag.Bypass, enabled)
    except Exception:
        pass


def _set_pending_state(node: hou.Node, *, pending: bool, reason: str = "") -> None:
    _set_node_bypass(node, pending)
    if pending:
        node.setComment(f"{SKD_PENDING_COMMENT_PREFIX} {reason}")
        return
    if node.comment().startswith(SKD_PENDING_COMMENT_PREFIX):
        node.setComment("")


def rebuild_managed_skd_variant_graph(output: hou.Node) -> tuple[str, ...]:
    """Rebuild a deterministic managed variant graph around an output node."""
    parent = output.parent()
    out_pos = output.position()
    declared_geo, declared_mat, sg_warnings = _discover_asset_variants_from_shotgrid()
    plan = variants.discover_build_plan(
        Path(hou.hscriptStringExpression("$HIP")),
        preferred_geo_variants=declared_geo or None,
        preferred_mat_variants=declared_mat or None,
    )
    warnings: list[str] = [*sg_warnings, *plan.warnings]

    owner_path = output.path()
    _clear_managed_variant_boxes(parent, owner_path=owner_path)
    _clear_managed_variant_nodes(
        parent, keep_paths={output.path()}, owner_path=owner_path
    )

    config = parent.createNode("sdm223::lnd_componentconfig")
    config.setName("config", unique_name=True)
    _mark_managed_variant_node(config, owner_path=owner_path)

    lookdev = create_skd_lookdev(parent, "lookdev")
    _mark_managed_variant_node(lookdev, owner_path=owner_path)

    env = parent.createNode("fetch")
    env.setName("env", unique_name=True)
    _mark_managed_variant_node(env, owner_path=owner_path)

    branch_outputs: list[tuple[str, hou.Node]] = []
    branch_bottom_y: float = out_pos.y() + 2.0
    geo_count = len(plan.geometry_variants)
    center_x = (geo_count - 1) / 2.0
    max_mats = max(
        (len(geo_plan.material_variants) for geo_plan in plan.geometry_variants),
        default=1,
    )
    branch_top_y = out_pos.y() + 5.2 + max(0.0, float(max_mats - 3) * 0.9)
    x_spacing = 4.4

    for geo_index, geo_plan in enumerate(plan.geometry_variants):
        geo_token = variants.node_token(geo_plan.name)
        geo_name = variants.node_token(
            geo_plan.name,
            fallback=SKD_COMPONENT_GEOMETRY_NAME,
        )
        branch_nodes: list[hou.Node] = []

        geo_node = create_skd_component_geometry(
            {},
            parent=parent,
            node_name=geo_name,
            geo_variant=geo_plan.name,
            source_expression=variants.to_hip_expression(
                geo_plan.source_path,
                hip_root=plan.hip_root,
            ),
        )
        _mark_managed_variant_node(geo_node, owner_path=owner_path)
        branch_nodes.append(geo_node)
        _set_pending_state(
            geo_node,
            pending=not geo_plan.source_exists,
            reason=(
                f"Missing geometry publish: "
                f"{variants.to_hip_expression(geo_plan.source_path, hip_root=plan.hip_root)}"
            ),
        )

        branch_x = (geo_index - center_x) * x_spacing
        geo_node.setPosition(hou.Vector2(out_pos.x() + branch_x, branch_top_y))
        branch_tail: hou.Node = geo_node

        single_branch = geo_count == 1 and len(geo_plan.material_variants) == 1
        for mat_index, mat_variant in enumerate(geo_plan.material_variants):
            mat_token = variants.node_token(mat_variant)
            matlib_name = (
                "matlib" if single_branch else f"matlib_{geo_token}_{mat_token}"
            )
            cmat_name = (
                "material" if single_branch else f"material_{geo_token}_{mat_token}"
            )

            matlib = create_skd_matlib(parent, matlib_name)
            _mark_managed_variant_node(matlib, owner_path=owner_path)
            branch_nodes.append(matlib)
            _set_matlib_variant_selection(
                matlib, geo_variant=geo_plan.name, mat_variant=mat_variant
            )

            cmat = create_skd_component_material(
                {},
                parent=parent,
                node_name=cmat_name,
                geo_variant_name=geo_plan.name,
                variant_name=mat_variant,
                use_input_variant_expression=False,
            )
            _mark_managed_variant_node(cmat, owner_path=owner_path)
            branch_nodes.append(cmat)

            cmat.setInput(0, branch_tail)
            cmat.setInput(1, matlib)
            branch_tail = cmat

            is_texture_published = mat_variant in geo_plan.existing_material_variants
            combo_missing = (not geo_plan.source_exists) or (not is_texture_published)
            _set_pending_state(
                cmat,
                pending=combo_missing,
                reason=(
                    f"Missing texture publish for geo='{geo_plan.name}' mat='{mat_variant}'"
                    if geo_plan.source_exists
                    else f"Missing geometry publish for geo='{geo_plan.name}'"
                ),
            )
            _set_pending_state(
                matlib,
                pending=not is_texture_published,
                reason=f"Awaiting textures for geo='{geo_plan.name}' mat='{mat_variant}'",
            )

            mat_y = branch_top_y - 1.4 - mat_index * 1.6
            cmat.setPosition(hou.Vector2(out_pos.x() + branch_x, mat_y))
            matlib.setPosition(hou.Vector2(out_pos.x() + branch_x + 1.7, mat_y + 0.8))
            branch_bottom_y = min(branch_bottom_y, mat_y)

            if is_texture_published:
                _rebuild_matlib_for_variant(
                    matlib,
                    geo_variant=geo_plan.name,
                    mat_variant=mat_variant,
                    warnings=warnings,
                )

        branch_bottom_y = min(branch_bottom_y, branch_top_y)
        _create_managed_variant_box(
            parent,
            owner_path=owner_path,
            name=f"geo_branch_{geo_token}",
            label=f"Geo Variant: {geo_plan.name}",
            nodes=branch_nodes,
        )
        branch_outputs.append((geo_plan.name, branch_tail))

    if not branch_outputs:
        raise RuntimeError("Variant graph generation produced no geometry branches")

    upstream: hou.Node = branch_outputs[0][1]
    if len(branch_outputs) > 1:
        geo_variants = parent.createNode("componentgeometryvariants")
        geo_variants.setName("geo_variants", unique_name=True)
        _mark_managed_variant_node(geo_variants, owner_path=owner_path)
        geo_variants.setPosition(hou.Vector2(out_pos.x(), branch_bottom_y - 1.4))

        for index, (_, branch) in enumerate(branch_outputs):
            geo_variants.setInput(index, branch)

        _set_parm_if_exists(geo_variants, "variantset", "geo")
        _set_parm_if_exists(geo_variants, "variantnamesrc", 0)
        _set_parm_if_exists(geo_variants, "variantcount", len(plan.geometry_variants))
        for index, (geo_name, _) in enumerate(branch_outputs, start=1):
            _set_parm_if_exists(geo_variants, f"variantname{index}", geo_name)

        upstream = geo_variants

    config.setInput(0, upstream)
    output.setInput(0, config)
    output.setInput(1, env)
    lookdev.setInput(0, output)
    _set_parm_if_exists(env, "loppath", f"../{lookdev.name()}/OUT_ENV")

    publish_anchor_y = upstream.position().y() - 2.0
    config.setPosition(hou.Vector2(out_pos.x(), publish_anchor_y))
    output.setPosition(hou.Vector2(out_pos.x(), publish_anchor_y - 1.6))
    env.setPosition(hou.Vector2(out_pos.x() + 2.1, publish_anchor_y - 0.9))
    lookdev.setPosition(hou.Vector2(out_pos.x(), publish_anchor_y - 3.3))

    _set_variant_generation_warnings(output, warnings)
    return tuple(warnings)


def create_skd_component_builder(
    kwargs: dict, parent: Optional[hou.Node] = None
) -> hou.Node:
    """Build the standard SKD Solaris component network."""
    out = _create_component_output_node(kwargs=kwargs, parent=parent)
    out.setColor(hou.Color((0.616, 0.871, 0.769)))
    _configure_component_output_defaults(out)
    warnings = rebuild_managed_skd_variant_graph(out)

    _mark_managed_builder(out)

    # Set first managed geometry node as the selected node.
    p = parent or out.parent()
    first_geo = _first_managed_geometry_node(p, owner_path=out.path())
    if first_geo is not None:
        first_geo.setSelected(True, clear_all_selected=True)
    else:
        out.setSelected(True, clear_all_selected=True)

    if warnings:
        log.warning(
            "SKD Component Builder created with %d variant warning(s).",
            len(warnings),
        )

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
