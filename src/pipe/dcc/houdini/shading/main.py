"""SKD MatLib: build RenderMan and USD Preview materials from published textures.

The flow is one straight line:

    published texture files   (textures.published_materials)
    -> MaterialSpec per texture set
    -> MaterialGraphBuilder creates only the nodes each spec needs
    -> the HDA's `errors` LOP checks every texture path still relocates

`matlib_*` and `MatlibErrorChecker` are the SKD_MatLib HDA's entry points; see
the note on `MatlibErrorChecker` before renaming any of them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import hou
from env_sg import DB_Config

from pipe.core.shotgrid import Asset, ShotGrid

from . import textures, variants

log = logging.getLogger(__name__)

_MATLIB_TYPE = "materiallibrary"
_MATLIB_NAME = "Material_Library"
_NO_TEXTURES = "NO_EXPORTED_TEXTURES"

_GENERATED_KEY = "skd_matlib_generated"
_GENERATED_VALUE = "1"

# RenderMan's material builder ships exactly one output node of type `collect`.
_BUILDER_OUTPUT = "output_collect"
# PxrLayerMixer mixes a base layer plus this many stacked layers.
_MIXER_SLOTS = 4

_MATERIAL_Y_STEP = 3.5
_LAYER_Y_STEP = 8.0
_PREVIEW_UV_PRIMVAR = "preview_uv"

# RenderMan colour-config aliases. Published `.tex` colour maps are already
# ACEScg, so they are tagged "rendering" rather than converted on read.
_COLOR_SPACE = "rendering"
_DATA_SPACE = "data"

_NODE_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True)
class _PreviewInput:
    """How one published preview map drives UsdPreviewSurface.

    `connections` pairs a UsdPreviewSurface input with a usduvtexture output.
    `source_colorspace` is a USD Preview Material spec token, not an OCIO name.
    """

    map_name: str
    connections: tuple[tuple[str, str], ...]
    source_colorspace: str
    row_offset: float
    scale: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    bias: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


_PREVIEW_INPUTS = (
    _PreviewInput("DiffuseColor", (("diffuseColor", "rgb"),), "sRGB", 2.0),
    _PreviewInput(
        "ORM",
        (("occlusion", "r"), ("roughness", "g"), ("metallic", "b")),
        "raw",
        0.0,
    ),
    _PreviewInput("Emissive", (("emissiveColor", "rgb"),), "sRGB", -2.0),
    # Normal maps store 0..1; UsdPreviewSurface wants -1..1 tangent space.
    _PreviewInput(
        "NormalDX",
        (("normal", "rgb"),),
        "raw",
        -4.0,
        scale=(2.0, 2.0, 2.0, 1.0),
        bias=(-1.0, -1.0, -1.0, 0.0),
    ),
)


if {preview.map_name for preview in _PREVIEW_INPUTS} != set(textures.PREVIEW.maps):
    # Drift here is silent: a discovered map with no entry is simply never wired.
    raise ImportError(
        "_PREVIEW_INPUTS and textures.PREVIEW.maps disagree, so a published "
        "preview map would be discovered and then dropped"
    )


def _parm(node: hou.Node, name: str) -> hou.Parm:
    """A parameter the node type guarantees.

    Missing means the DCC changed under us, so fail loudly with the node path
    rather than silently building a material with the value left at default.
    """
    parm = node.parm(name)
    if parm is None:
        raise hou.OperationFailed(
            f"{node.path()} ({node.type().name()}) has no '{name}' parameter"
        )
    return parm


def _parm_tuple(node: hou.Node, name: str) -> hou.ParmTuple:
    parm_tuple = node.parmTuple(name)
    if parm_tuple is None:
        raise hou.OperationFailed(
            f"{node.path()} ({node.type().name()}) has no '{name}' parameter"
        )
    return parm_tuple


def _node_name(value: str) -> str:
    """Node-safe form of a texture-set or layer name, preserving case."""
    cleaned = re.sub(r"_+", "_", _NODE_UNSAFE_RE.sub("_", value.strip())).strip("_")
    if not cleaned:
        return "material"
    return f"m_{cleaned}" if cleaned[0].isdigit() else cleaned


class MaterialGraphBuilder:
    """Creates the generated material graphs inside a `materiallibrary` LOP.

    Every node it creates is tagged, and every rebuild destroys the previous
    generation first. Materials an artist authored by hand are left alone.
    """

    def __init__(self, matlib: hou.Node) -> None:
        self._matlib = matlib

    def rebuild(
        self, materials: Sequence[textures.MaterialSpec], *, build_preview: bool
    ) -> None:
        for child in self._matlib.children():
            if child.userData(_GENERATED_KEY) == _GENERATED_VALUE:
                child.destroy()
        for index, material in enumerate(materials):
            self._build_material(
                material, y=-index * _MATERIAL_Y_STEP, build_preview=build_preview
            )

    def _build_material(
        self, material: textures.MaterialSpec, *, y: float, build_preview: bool
    ) -> None:
        builder = self._create(
            self._matlib,
            "pxrmaterialbuilder",
            f"MAT_{_node_name(material.texture_set)}",
            (0.0, y),
        )
        # The HDA's Material Library collects material-flagged children (matflag1).
        builder.setMaterialFlag(True)  # ty:ignore[unresolved-attribute]
        output = self._material_output(builder)

        surface, shader_nodes = self._build_renderman_shader(builder, material)
        preview_row = -(len(material.layers) * _LAYER_Y_STEP + 6.0)
        preview_surface, preview_nodes = (
            self._build_preview_shader(builder, material, preview_row)
            if build_preview
            else (None, [])
        )

        output.setInput(0, surface, 0)
        if preview_surface is not None:
            output.setInput(1, preview_surface, 0)

        self._group(builder, "RenderMan", (0.22, 0.40, 0.78), [*shader_nodes, output])
        self._group(builder, "UsdPreview", (0.86, 0.78, 0.28), preview_nodes)

    def _build_renderman_shader(
        self, builder: hou.Node, material: textures.MaterialSpec
    ) -> tuple[hou.Node, list[hou.Node]]:
        suffix = _node_name(material.texture_set)
        mixer = self._create(
            builder, "pxrlayermixer::3.0", f"{suffix}_LayerMixer", (8.0, 0.0)
        )
        surface = self._create(
            builder, "pxrlayersurface::3.0", f"{suffix}_PxrLayerSurface", (11.0, 0.0)
        )
        surface.setInput(0, mixer, 0)

        layer_specs = material.layers
        if len(layer_specs) > _MIXER_SLOTS + 1:
            log.warning(
                "Texture set '%s' publishes %d layers but PxrLayerMixer mixes %d; "
                "the remaining layers are not shaded.",
                material.texture_set,
                len(layer_specs),
                _MIXER_SLOTS + 1,
            )
            layer_specs = layer_specs[: _MIXER_SLOTS + 1]

        nodes = [mixer, surface]
        for index, layer_spec in enumerate(layer_specs):
            layer, layer_nodes = self._build_layer(
                builder,
                layer_spec,
                f"{suffix}_{_node_name(layer_spec.name)}",
                y=-index * _LAYER_Y_STEP,
            )
            nodes.extend(layer_nodes)
            slot = "baselayer" if index == 0 else f"layer{index}"
            mixer.setNamedInput(slot, layer, "pxrMaterialOut")

        # Stated for every slot so an unconnected one can never stay enabled.
        for slot in range(1, _MIXER_SLOTS + 1):
            _parm(mixer, f"layer{slot}Enabled").set(slot < len(layer_specs))
        return surface, nodes

    def _build_layer(
        self,
        builder: hou.Node,
        layer_spec: textures.LayerSpec,
        suffix: str,
        *,
        y: float,
    ) -> tuple[hou.Node, list[hou.Node]]:
        """A PxrLayer and the texture chain feeding it.

        A map that was never published gets no node at all: an empty texture node
        renders black and reads as a broken filepath to the shipping check.
        """
        maps = layer_spec.render_maps
        layer = self._create(builder, "pxrlayer::3.0", f"Layer_{suffix}", (1.0, y))
        _parm(layer, "enableSpecular").set(True)
        _parm(layer, "specularGain").set(1.0)

        base_color = self._texture(
            builder,
            "pxrtexture::3.0",
            f"BaseColor_{suffix}",
            maps.get("BaseColor"),
            (-5.0, y + 3.0),
            colorspace=_COLOR_SPACE,
        )
        metallic = self._texture(
            builder,
            "pxrtexture::3.0",
            f"Metallic_{suffix}",
            maps.get("Metallic"),
            (-5.0, y + 0.5),
            colorspace=_DATA_SPACE,
        )
        roughness = self._texture(
            builder,
            "pxrtexture::3.0",
            f"Roughness_{suffix}",
            maps.get("SpecularRoughness"),
            (-8.0, y - 2.0),
            colorspace=_DATA_SPACE,
        )
        normal = self._texture(
            builder,
            "pxrnormalmap::3.0",
            f"Normal_{suffix}",
            maps.get("Normal"),
            (-2.0, y - 3.5),
            colorspace=_DATA_SPACE,
        )

        nodes: list[hou.Node | None] = [layer, base_color, metallic, roughness, normal]
        if base_color is not None or metallic is not None:
            nodes.append(
                self._insert_metallic_workflow(
                    builder, layer, suffix, y, base_color, metallic
                )
            )
        if roughness is not None:
            nodes.append(
                self._insert_roughness_remap(builder, layer, suffix, y, roughness)
            )
        if normal is not None:
            layer.setNamedInput("bumpNormal", normal, "resultN")
        return layer, [node for node in nodes if node is not None]

    def _insert_metallic_workflow(
        self,
        builder: hou.Node,
        layer: hou.Node,
        suffix: str,
        y: float,
        base_color: hou.Node | None,
        metallic: hou.Node | None,
    ) -> hou.Node:
        """Split base colour and metallic into PxrLayer's diffuse/specular inputs."""
        workflow = self._create(
            builder,
            "pxrmetallicworkflow::3.0",
            f"MetallicWorkflow_{suffix}",
            (-2.0, y + 1.0),
        )
        if base_color is not None:
            workflow.setNamedInput("baseColor", base_color, "resultRGB")
        else:
            # PxrMetallicWorkflow.baseColor defaults to RenderMan's placeholder
            # blue, and we are about to drive the layer's diffuse from it. Hand
            # it the layer's own default so an unpublished base colour changes
            # nothing except the specular response.
            _parm_tuple(workflow, "baseColor").set(
                _parm_tuple(layer, "diffuseColor").evalAsFloats()
            )
        if metallic is not None:
            workflow.setNamedInput("metallic", metallic, "resultR")
        layer.setNamedInput("diffuseColor", workflow, "resultDiffuseRGB")
        layer.setNamedInput("specularFaceColor", workflow, "resultSpecularFaceRGB")
        layer.setNamedInput("specularEdgeColor", workflow, "resultSpecularEdgeRGB")
        return workflow

    def _insert_roughness_remap(
        self,
        builder: hou.Node,
        layer: hou.Node,
        suffix: str,
        y: float,
        roughness: hou.Node,
    ) -> hou.Node:
        remap = self._create(
            builder, "pxrremap::3.0", f"RoughnessRemap_{suffix}", (-5.0, y - 2.0)
        )
        remap.setNamedInput("inputRGB", roughness, "resultRGB")
        layer.setNamedInput("specularRoughness", remap, "resultR")
        return remap

    def _build_preview_shader(
        self, builder: hou.Node, material: textures.MaterialSpec, row_y: float
    ) -> tuple[hou.Node | None, list[hou.Node]]:
        if not material.preview_maps:
            return None, []

        suffix = _node_name(material.texture_set)
        surface = self._create(
            builder, "usdpreviewsurface", f"{suffix}_UsdPreviewSurface", (11.0, row_y)
        )
        uv_reader = self._create(
            builder, "usdprimvarreader", f"{suffix}_PreviewUv", (3.0, row_y)
        )
        # "float2" is the signature token that makes the output a UV pair; a token
        # Houdini does not recognise silently leaves the output a single float.
        _parm(uv_reader, "signature").set("float2")
        _parm(uv_reader, "varname").set(_PREVIEW_UV_PRIMVAR)

        nodes = [surface, uv_reader]
        for preview in _PREVIEW_INPUTS:
            path = material.preview_maps.get(preview.map_name)
            if path is None:
                continue
            texture = self._create(
                builder,
                "usduvtexture::2.0",
                f"{preview.map_name}_{suffix}_PreviewTex",
                (6.0, row_y + preview.row_offset),
            )
            _parm(texture, "file").set(path)
            _parm(texture, "sourceColorSpace").set(preview.source_colorspace)
            _parm_tuple(texture, "scale").set(preview.scale)
            _parm_tuple(texture, "bias").set(preview.bias)
            texture.setNamedInput("st", uv_reader, "result")
            for surface_input, texture_output in preview.connections:
                surface.setNamedInput(surface_input, texture, texture_output)
            nodes.append(texture)
        return surface, nodes

    def _texture(
        self,
        builder: hou.Node,
        node_type: str,
        name: str,
        path: str | None,
        position: tuple[float, float],
        *,
        colorspace: str,
    ) -> hou.Node | None:
        """None when the map was never published, so the caller wires nothing."""
        if path is None:
            return None
        node = self._create(builder, node_type, name, position)
        _parm(node, "filename").set(path)
        _parm(node, "filename_colorspace").set(colorspace)
        return node

    @staticmethod
    def _material_output(builder: hou.Node) -> hou.Node:
        """The output RenderMan's builder ships with.

        Substituting our own `collect` would build a material that looks right
        and carries an unknown output contract, so refuse before anything is
        wired instead.
        """
        output = builder.node(_BUILDER_OUTPUT)
        if output is None:
            raise hou.OperationFailed(
                f"{builder.path()} ({builder.type().name()}) has no "
                f"'{_BUILDER_OUTPUT}' child; this RenderMan build is unsupported"
            )
        return output

    @staticmethod
    def _group(
        builder: hou.Node,
        name: str,
        color: tuple[float, float, float],
        nodes: Sequence[hou.Node],
    ) -> None:
        """Box the nodes for navigation."""
        if not nodes:
            return
        box = builder.createNetworkBox()
        box.setName(name, unique_name=True)
        box.setComment(name)
        box.setColor(hou.Color(color))
        for node in nodes:
            box.addItem(node)
        box.fitAroundContents()

    def _create(
        self,
        parent: hou.Node,
        node_type: str,
        name: str,
        position: tuple[float, float],
    ) -> hou.Node:
        node = parent.createNode(node_type)
        node.setName(name, unique_name=True)
        node.setUserData(_GENERATED_KEY, _GENERATED_VALUE)
        node.setPosition(hou.Vector2(*position))
        return node


class PathFault(Enum):
    """Why a texture path will not survive the asset being moved or shared."""

    NOT_PORTABLE = auto()
    OUTSIDE_ASSET = auto()


@dataclass(frozen=True)
class TexturePathProblem:
    """One offending texture parm, as facts. Rendering lives in the message."""

    node_path: str
    parm_name: str
    authored: str
    resolved: str
    fault: PathFault


_ASSET_ROOT_VARIABLES = ("$HIP", "$JOB")
_PORTABLE_PREFIXES = ("$HIP/", "$JOB/", "${HIP}/", "${JOB}/")
_MAX_REPORTED = 10
_FAULT_TEXT = {
    PathFault.NOT_PORTABLE: "must start with $HIP or $JOB",
    PathFault.OUTSIDE_ASSET: "resolves outside this asset",
}


def texture_path_problems(matlib: hou.Node) -> list[TexturePathProblem]:
    """Texture parms under `matlib` that will not resolve if the asset moves."""
    roots = _asset_roots()
    problems: list[TexturePathProblem] = []
    for node in (matlib, *matlib.allSubChildren()):
        for parm in _file_reference_parms(node):
            authored = parm.unexpandedString().strip()
            resolved = str(parm.eval()).strip()
            fault = _path_fault(authored, resolved, roots)
            if fault is not None:
                problems.append(
                    TexturePathProblem(
                        node_path=matlib.relativePathTo(node),
                        parm_name=parm.name(),
                        authored=authored,
                        resolved=resolved,
                        fault=fault,
                    )
                )
    return problems


def matlib_path_error(matlib: hou.Node) -> str:
    """Artist-facing message for the HDA's error LOP; empty when every path is fine."""
    problems = texture_path_problems(matlib)
    if not problems:
        return ""

    lines = [
        f"{len(problems)} texture path(s) will break when this asset is moved or "
        "opened by someone else. Every texture must be read through $HIP or $JOB.",
        "",
    ]
    for problem in problems[:_MAX_REPORTED]:
        detail = problem.authored
        if problem.resolved and problem.resolved != problem.authored:
            detail = f"{problem.authored}  ->  {problem.resolved}"
        lines.append(
            f"    {problem.node_path} ({problem.parm_name}) "
            f"{_FAULT_TEXT[problem.fault]}:"
        )
        lines.append(f"        {detail}")
    if len(problems) > _MAX_REPORTED:
        lines.append(f"    ...and {len(problems) - _MAX_REPORTED} more.")
    lines.append("")
    lines.append(
        "Republish these textures into this asset's publish/tex folder, "
        "then press Rebuild Materials."
    )
    return "\n".join(lines)


def _path_fault(
    authored: str, resolved: str, roots: Sequence[Path]
) -> PathFault | None:
    """Portability and containment are separate faults; the authored one wins."""
    if not authored:
        # No texture assigned. That map was never published, which is a lookdev
        # gap rather than a path that breaks when the asset moves.
        return None
    if not authored.startswith(_PORTABLE_PREFIXES):
        return PathFault.NOT_PORTABLE
    if resolved and roots and not any(_is_within(resolved, root) for root in roots):
        return PathFault.OUTSIDE_ASSET
    return None


def _is_within(path: str, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root)


def _asset_roots() -> list[Path]:
    """Resolved $HIP and $JOB: the folders a published texture may live under."""
    expanded = (hou.hscriptStringExpression(name) for name in _ASSET_ROOT_VARIABLES)
    return [Path(value).resolve() for value in expanded if value.strip()]


def _file_reference_parms(node: hou.Node) -> list[hou.Parm]:
    """Every parm Houdini types as a file reference."""
    return [
        parm
        for parm in node.parms()
        if isinstance(template := parm.parmTemplate(), hou.StringParmTemplate)
        and template.stringType() == hou.stringParmType.FileReference
        and not parm.isAtDefault()
    ]


class MatlibErrorChecker:
    """Entry point for the HDA's `errors` LOP. Do not rename."""

    @staticmethod
    def CheckFilepathsRelative(matlib: hou.Node) -> int:  # noqa: N802
        return 1 if texture_path_problems(matlib) else 0


class MatlibManager:
    """Backs the SKD_MatLib HDA's parameters and its Rebuild Materials button."""

    def __init__(self, node: hou.LopNode) -> None:
        self._node = node

    def geo_variants(self) -> list[str]:
        asset = self._asset()
        return _variant_names(asset.geometry_variants if asset else None, "main")

    def mat_variants(self) -> list[str]:
        asset = self._asset()
        return _variant_names(asset.material_variants if asset else None, _NO_TEXTURES)

    def initialize_defaults(self) -> None:
        _set_string_parm(self._node, "geo_var", self.geo_variants()[0])
        _set_string_parm(self._node, "mat_var", self.mat_variants()[0])

    def rebuild(self) -> None:
        matlib = self._material_library()
        if matlib is None:
            log.error(
                "No %s node inside %s, so there is nothing to rebuild.",
                _MATLIB_TYPE,
                self._node.path(),
            )
            return

        geo_variant = _string_parm(self._node, "geo_var", "main")
        mat_variant = _string_parm(self._node, "mat_var", _NO_TEXTURES)
        # Component Material expects /ASSET/mtl/g_<geo>/v_<mat>/MAT_<texset>.
        _parm(matlib, "matpathprefix").set(
            variants.material_scope_path(mat_variant, geo_variant=geo_variant)
        )

        hip_root = Path(hou.hscriptStringExpression("$HIP"))
        tex_root = hip_root / variants.TEX_SOURCE_DIR / geo_variant / mat_variant
        materials = textures.published_materials(tex_root, hip_root=hip_root)
        if not materials:
            log.warning("No materials to build from %s", tex_root)

        MaterialGraphBuilder(matlib).rebuild(
            materials,
            build_preview=_toggle(self._node, "build_usd_preview", default=True),
        )

    def _asset(self) -> Asset | None:
        try:
            connection = ShotGrid.connect(DB_Config)
            return connection.get_asset(name=str(hou.contextOption("ASSET")))
        except Exception:
            # Variant menus and defaults must still work when ShotGrid is
            # unreachable; the artist can type a variant name by hand.
            log.exception(
                "Could not resolve the ASSET context option for %s", self._node.path()
            )
            return None

    def _material_library(self) -> hou.Node | None:
        by_name = self._node.node(_MATLIB_NAME)
        if by_name is not None and by_name.type().name() == _MATLIB_TYPE:
            return by_name
        return next(
            (
                child
                for child in self._node.children()
                if child.type().name() == _MATLIB_TYPE
            ),
            None,
        )


def _variant_names(declared: Iterable[str] | None, fallback: str) -> list[str]:
    named = sorted((name for name in (declared or ()) if name), key=str.casefold)
    return named or [fallback]


def _menu_entries(values: list[str]) -> list[str]:
    """Houdini menus want alternating token and label entries."""
    return [entry for value in values for entry in (value, value)]


def _string_parm(node: hou.LopNode, name: str, fallback: str) -> str:
    parm = node.parm(name)
    if parm is None:
        return fallback
    return parm.evalAsString().strip() or fallback


def _set_string_parm(node: hou.LopNode, name: str, value: str) -> None:
    parm = node.parm(name)
    if parm is not None:
        parm.set(value)


def _toggle(node: hou.LopNode, name: str, *, default: bool) -> bool:
    parm = node.parm(name)
    return default if parm is None else bool(parm.evalAsInt())


def matlib_on_created(node: hou.LopNode) -> None:
    MatlibManager(node).initialize_defaults()


def matlib_geo_variant_menu(node: hou.LopNode) -> list[str]:
    return _menu_entries(MatlibManager(node).geo_variants())


def matlib_mat_variant_menu(node: hou.LopNode) -> list[str]:
    return _menu_entries(MatlibManager(node).mat_variants())


def matlib_on_variant_changed(node: hou.LopNode) -> None:
    if _toggle(node, "auto_rebuild", default=False):
        MatlibManager(node).rebuild()


def matlib_rebuild(node: hou.LopNode) -> None:
    MatlibManager(node).rebuild()
