from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import hou
from env_sg import DB_Config

from pipe.db import DB
from pipe.struct.db import Asset
from pipe.struct.material import MaterialInfo

from . import variants

log = logging.getLogger(__name__)

_MATLIB_NAME = "Material_Library"
_NO_TEXTURES = "NO_EXPORTED_TEXTURES"

_AUTO_TAG_KEY = "skd_matlib_generated"
_AUTO_TAG_VALUE = "1"
_BUILDER_OUTPUT_NODE_NAMES = (
    "output_collect",
    "output_collect1",
    "suboutput1",
    "output1",
    "OUT_material",
    "OUT",
)
_MATERIAL_BUILDER_Y_STEP = 3.5

_RENDER_MAPS = ("BaseColor", "SpecularRoughness", "Normal", "Metallic")
_PREVIEW_MAPS = ("DiffuseColor", "ORM", "Emissive", "NormalDX")
_SUPPORTED_MAPS = tuple(sorted({*_RENDER_MAPS, *_PREVIEW_MAPS}))
_MAP_NAME_LOOKUP = {name.lower(): name for name in _SUPPORTED_MAPS}
_NODE_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")
_UDIM_RE = re.compile(r"\.(?P<udim>\d{4})(?=\.[^.]+$)")
_MAP_PATTERN = "|".join(sorted(_SUPPORTED_MAPS, key=len, reverse=True))
_TEX_FILE_RE = re.compile(
    rf"^(?P<tex_set>.+?)_(?P<map>{_MAP_PATTERN})(?:_[^.]+)?"
    rf"(?:\.(?P<udim>\d{{4}}))?\.(?P<ext>[A-Za-z0-9]+)$",
    flags=re.IGNORECASE,
)


def _sanitize_node_name(name: str) -> str:
    cleaned = _NODE_SAFE_RE.sub("_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "material"
    if cleaned[0].isdigit():
        return f"m_{cleaned}"
    return cleaned


def _asset_menu_format(values: list[str]) -> list[str]:
    return [entry for value in values for entry in (value, value)]


@dataclass(frozen=True)
class TextureCandidate:
    tex_set: str
    map_name: str
    path: Path
    extension: str
    udim: str | None
    priority: int


@dataclass(frozen=True)
class LayerDiscovery:
    name: str
    path: Path
    metadata_texture_sets: frozenset[str]
    render_candidates: tuple[TextureCandidate, ...]
    preview_candidates: tuple[TextureCandidate, ...]


@dataclass(frozen=True)
class LayerMaterialSpec:
    name: str
    render_maps: dict[str, str]


@dataclass(frozen=True)
class MaterialSpec:
    texture_set: str
    layers: tuple[LayerMaterialSpec, ...]
    preview_maps: dict[str, str]


@dataclass(frozen=True)
class MaterialLibrarySpec:
    geo_variant: str
    mat_variant: str
    materials: tuple[MaterialSpec, ...]


class MatlibDiscovery:
    """Discovery layer: read publish/tex + mat.json and collect map candidates."""

    def __init__(self, hip_root: Path, geo_variant: str, mat_variant: str) -> None:
        self._hip_root = hip_root
        self._geo_variant = geo_variant.strip()
        self._mat_variant = mat_variant.strip()

    @property
    def variant_root(self) -> Path:
        return (
            self._hip_root / "publish" / "tex" / self._geo_variant / self._mat_variant
        )

    def discover_layers(self) -> list[LayerDiscovery]:
        root = self.variant_root
        if not root.exists():
            log.warning("Texture publish path does not exist: %s", root)
            return []

        layer_dirs = [
            p
            for p in sorted(root.iterdir(), key=lambda path: path.name.casefold())
            if p.is_dir() and not p.name.startswith(".")
        ]
        discoveries: list[LayerDiscovery] = []
        for layer_dir in layer_dirs:
            discoveries.append(self._discover_layer(layer_dir))
        return discoveries

    def _discover_layer(self, layer_dir: Path) -> LayerDiscovery:
        metadata_texture_sets = self._read_mat_info(layer_dir / "mat.json")

        render_candidates: list[TextureCandidate] = []
        render_candidates.extend(
            self._parse_candidates(
                layer_dir, priority=0, allowed_maps=set(_RENDER_MAPS)
            )
        )
        render_candidates.extend(
            self._parse_candidates(
                layer_dir / "_src", priority=1, allowed_maps=set(_RENDER_MAPS)
            )
        )

        preview_candidates: list[TextureCandidate] = []
        preview_candidates.extend(
            self._parse_candidates(
                layer_dir / "_preview", priority=0, allowed_maps=set(_PREVIEW_MAPS)
            )
        )
        preview_candidates.extend(
            self._parse_candidates(
                layer_dir / "_src", priority=1, allowed_maps=set(_PREVIEW_MAPS)
            )
        )

        return LayerDiscovery(
            name=layer_dir.name,
            path=layer_dir,
            metadata_texture_sets=frozenset(metadata_texture_sets),
            render_candidates=tuple(render_candidates),
            preview_candidates=tuple(preview_candidates),
        )

    @staticmethod
    def _read_mat_info(path: Path) -> set[str]:
        if not path.exists():
            return set()
        try:
            return set(
                MaterialInfo.from_json(path.read_text(encoding="utf-8")).tex_sets
            )
        except Exception:
            log.exception("Failed to parse mat.json at %s", path)
            return set()

    @staticmethod
    def _parse_candidates(
        directory: Path, *, priority: int, allowed_maps: set[str]
    ) -> list[TextureCandidate]:
        if not directory.exists() or not directory.is_dir():
            return []

        parsed: list[TextureCandidate] = []
        for item in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
            if not item.is_file():
                continue

            candidate = MatlibDiscovery._parse_texture_file(item, priority=priority)
            if candidate is None:
                continue
            if candidate.map_name not in allowed_maps:
                continue

            parsed.append(candidate)
        return parsed

    @staticmethod
    def _parse_texture_file(path: Path, *, priority: int) -> TextureCandidate | None:
        match = _TEX_FILE_RE.match(path.name)
        if not match:
            return None

        tex_set = match.group("tex_set").strip()
        raw_map = match.group("map").strip().lower()
        map_name = _MAP_NAME_LOOKUP.get(raw_map)
        if not tex_set or map_name is None:
            return None

        return TextureCandidate(
            tex_set=tex_set,
            map_name=map_name,
            path=path,
            extension=(path.suffix.lstrip(".").lower()),
            udim=match.group("udim"),
            priority=priority,
        )


class MatlibSpecBuilder:
    """Spec layer: convert discovered files into deterministic material specs."""

    def __init__(self, hip_root: Path) -> None:
        self._hip_root = hip_root

    def build(
        self,
        *,
        geo_variant: str,
        mat_variant: str,
        layers: list[LayerDiscovery],
    ) -> MaterialLibrarySpec:
        ordered_layers = sorted(layers, key=lambda layer: layer.name.casefold())
        texture_sets = sorted(
            self._collect_texture_sets(ordered_layers), key=str.casefold
        )

        materials: list[MaterialSpec] = []
        for tex_set in texture_sets:
            layer_specs: list[LayerMaterialSpec] = []
            for layer in ordered_layers:
                layer_maps = self._render_maps_for_layer(layer, tex_set)
                if layer_maps:
                    layer_specs.append(
                        LayerMaterialSpec(name=layer.name, render_maps=layer_maps)
                    )

            if not layer_specs:
                log.warning(
                    "No render maps found for texture set %s (geo=%s mat=%s)",
                    tex_set,
                    geo_variant,
                    mat_variant,
                )
                continue

            preview_maps = self._preview_maps_for_tex_set(ordered_layers, tex_set)
            materials.append(
                MaterialSpec(
                    texture_set=tex_set,
                    layers=tuple(layer_specs),
                    preview_maps=preview_maps,
                )
            )

        return MaterialLibrarySpec(
            geo_variant=geo_variant,
            mat_variant=mat_variant,
            materials=tuple(materials),
        )

    def _collect_texture_sets(self, layers: list[LayerDiscovery]) -> set[str]:
        texture_sets: set[str] = set()
        for layer in layers:
            texture_sets.update(layer.metadata_texture_sets)
            texture_sets.update(
                candidate.tex_set for candidate in layer.render_candidates
            )
            texture_sets.update(
                candidate.tex_set for candidate in layer.preview_candidates
            )
        return texture_sets

    def _render_maps_for_layer(
        self, layer: LayerDiscovery, tex_set: str
    ) -> dict[str, str]:
        maps: dict[str, str] = {}
        for map_name in _RENDER_MAPS:
            candidates = [
                candidate
                for candidate in layer.render_candidates
                if candidate.tex_set == tex_set and candidate.map_name == map_name
            ]
            chosen = self._select_candidate(candidates, map_name)
            if chosen:
                maps[map_name] = self._candidate_path(chosen)
        return maps

    def _preview_maps_for_tex_set(
        self, layers: list[LayerDiscovery], tex_set: str
    ) -> dict[str, str]:
        preview_maps: dict[str, str] = {}
        for layer in layers:
            for map_name in _PREVIEW_MAPS:
                candidates = [
                    candidate
                    for candidate in layer.preview_candidates
                    if candidate.tex_set == tex_set and candidate.map_name == map_name
                ]
                chosen = self._select_candidate(candidates, map_name)
                if chosen:
                    # Later layers deterministically override earlier layers.
                    preview_maps[map_name] = self._candidate_path(chosen)
        return preview_maps

    def _candidate_path(self, candidate: TextureCandidate) -> str:
        expression = self._to_hip_expression(candidate.path)
        if candidate.udim:
            return _UDIM_RE.sub(".<UDIM>", expression)
        return expression

    def _to_hip_expression(self, path: Path) -> str:
        try:
            relative = path.relative_to(self._hip_root)
            return f"$HIP/{relative.as_posix()}"
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _select_candidate(
        candidates: list[TextureCandidate], map_name: str
    ) -> TextureCandidate | None:
        if not candidates:
            return None

        def rank(candidate: TextureCandidate) -> tuple[int, int, str]:
            return (
                candidate.priority,
                MatlibSpecBuilder._extension_rank(map_name, candidate.extension),
                candidate.path.name.casefold(),
            )

        return min(candidates, key=rank)

    @staticmethod
    def _extension_rank(map_name: str, extension: str) -> int:
        ext = extension.lower()
        if map_name == "Normal":
            order = ("b2r", "tex", "exr", "png", "jpg", "jpeg")
        elif map_name in _PREVIEW_MAPS:
            order = ("jpeg", "jpg", "png", "exr", "tex", "b2r")
        else:
            order = ("tex", "exr", "png", "jpg", "jpeg", "b2r")
        try:
            return order.index(ext)
        except ValueError:
            return len(order)


class MatlibNodeBuilder:
    """Node-builder layer: create deterministic material graphs in Material_Library."""

    def __init__(self, matlib: hou.Node) -> None:
        self._matlib = matlib

    def rebuild(self, spec: MaterialLibrarySpec, *, build_preview: bool) -> None:
        self._clear_generated_nodes()
        self._clear_generated_network_boxes()
        if not spec.materials:
            log.warning(
                "No materials discovered for geo=%s mat=%s",
                spec.geo_variant,
                spec.mat_variant,
            )
            return

        y_cursor = 0.0
        for material in spec.materials:
            builder = self._create_material_builder(material, y=-y_cursor)
            rm_surface, rm_nodes = self._build_renderman_graph(
                builder, material, row_y=0
            )
            preview_row_y = -(len(material.layers) * 8 + 6)
            preview_surface, preview_nodes = (
                self._build_preview_graph(builder, material, preview_row_y)
                if build_preview
                else (None, [])
            )
            collect = self._build_material_collect(
                builder,
                row_y=0,
                rm_surface=rm_surface,
                preview_surface=preview_surface,
            )
            if not self._is_builder_output_collect(collect):
                self._wire_material_builder_output(builder, collect)
            self._build_navigation_groups(
                builder, material, rm_nodes + [collect], preview_nodes
            )
            y_cursor += _MATERIAL_BUILDER_Y_STEP

    def _clear_generated_nodes(self) -> None:
        generated = [
            child
            for child in self._matlib.children()
            if child.userData(_AUTO_TAG_KEY) == _AUTO_TAG_VALUE
        ]
        for node in generated:
            node.destroy()

    def _clear_generated_network_boxes(self) -> None:
        if not hasattr(self._matlib, "networkBoxes"):
            return

        for net_box in self._matlib.networkBoxes():
            try:
                marked = cast(Any, net_box).userData(_AUTO_TAG_KEY) == _AUTO_TAG_VALUE
            except Exception:
                marked = False
            if marked:
                net_box.destroy()

    def _create_material_builder(self, material: MaterialSpec, *, y: float) -> hou.Node:
        tex_set_id = _sanitize_node_name(material.texture_set)
        mat_name = f"MAT_{tex_set_id}"
        builder = self._create_first_supported_node(
            self._matlib,
            ("pxrmaterialbuilder::3.0", "pxrmaterialbuilder"),
            mat_name,
        )
        if builder is None:
            log.warning(
                "pxrmaterialbuilder unavailable; falling back to subnet for %s",
                mat_name,
            )
            builder = self._create_node(self._matlib, "subnet", mat_name)

        builder.setPosition(hou.Vector2(0, y))
        self._set_material_flag(builder, True)
        self._prune_material_builder(builder)
        return builder

    def _build_renderman_graph(
        self, parent: hou.Node, material: MaterialSpec, row_y: int
    ) -> tuple[hou.Node, list[hou.Node]]:
        tex_set_id = _sanitize_node_name(material.texture_set)
        mixer = self._create_node(
            parent, "pxrlayermixer::3.0", f"{tex_set_id}_LayerMixer"
        )
        surface = self._create_node(
            parent, "pxrlayersurface::3.0", f"{tex_set_id}_PxrLayerSurface"
        )
        mixer.setPosition(hou.Vector2(8, row_y))
        surface.setPosition(hou.Vector2(11, row_y))
        surface.setInput(0, mixer, 0)

        all_nodes: list[hou.Node] = [mixer, surface]
        for layer_index, layer_spec in enumerate(material.layers):
            layer_node, layer_nodes = self._build_layer(
                parent, material.texture_set, layer_spec, row_y, layer_index
            )
            all_nodes.extend(layer_nodes)
            if layer_index == 0:
                mixer.setNamedInput("baselayer", layer_node, "pxrMaterialOut")
                self._set_parm_if_exists(mixer, "layer1Enabled", False)
            else:
                input_name = f"layer{layer_index}"
                mixer.setNamedInput(input_name, layer_node, "pxrMaterialOut")
                self._set_parm_if_exists(mixer, f"{input_name}Enabled", True)
        return surface, all_nodes

    def _build_layer(
        self,
        parent: hou.Node,
        tex_set_name: str,
        layer_spec: LayerMaterialSpec,
        row_y: int,
        layer_index: int,
    ) -> tuple[hou.Node, list[hou.Node]]:
        tex_set_id = _sanitize_node_name(tex_set_name)
        layer_id = _sanitize_node_name(layer_spec.name)
        layer_suffix = f"{tex_set_id}_{layer_id}"
        y = row_y - layer_index * 8

        roughness = self._create_node(
            parent, "pxrtexture::3.0", f"Roughness_{layer_suffix}"
        )
        roughness_remap = self._create_node(
            parent, "pxrremap::3.0", f"RoughnessRemap_{layer_suffix}"
        )
        color = self._create_node(
            parent, "pxrtexture::3.0", f"BaseColor_{layer_suffix}"
        )
        normal = self._create_node(
            parent, "pxrnormalmap::3.0", f"Normal_{layer_suffix}"
        )
        layer = self._create_node(parent, "pxrlayer::3.0", f"Layer_{layer_suffix}")
        metallic_workflow = self._create_node(
            parent, "pxrmetallicworkflow::3.0", f"MetallicWorkflow_{layer_suffix}"
        )
        metallic = self._create_node(
            parent, "pxrtexture::3.0", f"Metallic_{layer_suffix}"
        )

        roughness.setPosition(hou.Vector2(-8, y - 2))
        roughness_remap.setPosition(hou.Vector2(-5, y - 2))
        color.setPosition(hou.Vector2(-5, y + 3))
        metallic.setPosition(hou.Vector2(-5, y + 0.5))
        metallic_workflow.setPosition(hou.Vector2(-2, y + 1))
        normal.setPosition(hou.Vector2(-2, y - 3.5))
        layer.setPosition(hou.Vector2(1, y))

        roughness_remap.setNamedInput("inputRGB", roughness, "resultRGB")
        metallic_workflow.setNamedInput("baseColor", color, "resultRGB")
        metallic_workflow.setNamedInput("metallic", metallic, "resultR")
        layer.setNamedInput("diffuseColor", metallic_workflow, "resultDiffuseRGB")
        layer.setNamedInput(
            "specularFaceColor", metallic_workflow, "resultSpecularFaceRGB"
        )
        layer.setNamedInput(
            "specularEdgeColor", metallic_workflow, "resultSpecularEdgeRGB"
        )
        layer.setNamedInput("specularRoughness", roughness_remap, "resultR")
        layer.setNamedInput("bumpNormal", normal, "resultN")

        self._set_texture_filename(
            color, layer_spec.render_maps.get("BaseColor"), is_color=True
        )
        self._set_texture_filename(
            roughness, layer_spec.render_maps.get("SpecularRoughness"), is_color=False
        )
        self._set_texture_filename(
            normal, layer_spec.render_maps.get("Normal"), is_color=False
        )
        self._set_texture_filename(
            metallic, layer_spec.render_maps.get("Metallic"), is_color=False
        )

        self._set_parm_if_exists(layer, "enableSpecular", True)
        self._set_parm_if_exists(layer, "specularGain", 1.0)
        return layer, [
            roughness,
            roughness_remap,
            color,
            normal,
            layer,
            metallic_workflow,
            metallic,
        ]

    def _build_preview_graph(
        self, parent: hou.Node, material: MaterialSpec, row_y: int
    ) -> tuple[hou.Node | None, list[hou.Node]]:
        if not material.preview_maps:
            return None, []

        tex_set_id = _sanitize_node_name(material.texture_set)
        preview_surface = self._create_first_supported_node(
            parent,
            ("usdpreviewsurface", "usdpreviewsurface::2.0"),
            f"{tex_set_id}_UsdPreviewSurface",
        )
        if preview_surface is None:
            log.warning(
                "USD Preview Surface node type unavailable; skipping preview graph"
            )
            return None, []

        preview_surface.setPosition(hou.Vector2(11, row_y))

        diffuse = self._create_preview_texture(
            parent,
            tex_set_id,
            "Diffuse",
            material.preview_maps.get("DiffuseColor"),
            row_y + 2,
            color=True,
        )
        orm = self._create_preview_texture(
            parent,
            tex_set_id,
            "ORM",
            material.preview_maps.get("ORM"),
            row_y + 0,
            color=False,
        )
        emissive = self._create_preview_texture(
            parent,
            tex_set_id,
            "Emissive",
            material.preview_maps.get("Emissive"),
            row_y - 2,
            color=True,
        )
        normal = self._create_preview_texture(
            parent,
            tex_set_id,
            "Normal",
            material.preview_maps.get("NormalDX"),
            row_y - 4,
            color=False,
        )

        if diffuse:
            self._connect_named(
                preview_surface, "diffuseColor", diffuse, ("rgb", "resultRGB", "result")
            )
        if emissive:
            self._connect_named(
                preview_surface,
                "emissiveColor",
                emissive,
                ("rgb", "resultRGB", "result"),
            )
        if orm:
            self._connect_named(
                preview_surface, "opacity", orm, ("r", "outR", "resultR")
            )
            self._connect_named(
                preview_surface, "roughness", orm, ("g", "outG", "resultG")
            )
            self._connect_named(
                preview_surface, "metallic", orm, ("b", "outB", "resultB")
            )
        if normal:
            self._connect_named(
                preview_surface, "normal", normal, ("rgb", "resultRGB", "result")
            )
        preview_nodes = [preview_surface]
        preview_nodes.extend(
            [node for node in (diffuse, orm, emissive, normal) if node]
        )
        return preview_surface, preview_nodes

    def _build_material_collect(
        self,
        parent: hou.Node,
        row_y: int,
        rm_surface: hou.Node,
        preview_surface: hou.Node | None,
    ) -> hou.Node:
        existing_collect = self._find_builder_output_collect(parent)
        if existing_collect is not None:
            existing_collect.setPosition(hou.Vector2(14, row_y))
            self._disconnect_inputs(existing_collect)
            existing_collect.setInput(0, rm_surface, 0)
            if preview_surface is not None:
                existing_collect.setInput(1, preview_surface, 0)
            return existing_collect

        collect = self._create_first_supported_node(
            parent, ("collect",), "FinalCollect"
        )
        if collect is None:
            # Fallback: if collect is unavailable, expose RenderMan surface directly.
            self._set_material_flag(rm_surface, True)
            return rm_surface

        collect.setPosition(hou.Vector2(14, row_y))
        collect.setInput(0, rm_surface, 0)
        if preview_surface is not None:
            collect.setInput(1, preview_surface, 0)

        return collect

    def _find_builder_output_collect(self, builder: hou.Node) -> hou.Node | None:
        for node_name in ("output_collect", "output_collect1"):
            node = builder.node(node_name)
            if node is not None:
                return node
        return None

    def _is_builder_output_collect(self, node: hou.Node) -> bool:
        return node.name() in {"output_collect", "output_collect1"}

    def _build_navigation_groups(
        self,
        parent: hou.Node,
        material: MaterialSpec,
        rm_nodes: list[hou.Node],
        preview_nodes: list[hou.Node],
    ) -> None:
        tex_set_id = _sanitize_node_name(material.texture_set)

        rm_box = self._create_network_box(parent, f"{tex_set_id}_PxrSurface_Group")
        if rm_box is not None:
            rm_box.setColor(hou.Color((0.22, 0.40, 0.78)))
            self._set_network_box_label(rm_box, "RenderMan Shader")
            for node in rm_nodes:
                rm_box.addItem(node)
            self._fit_network_box(rm_box)

        if preview_nodes:
            preview_box = self._create_network_box(
                parent, f"{tex_set_id}_UsdPreviewSurface_Group"
            )
            if preview_box is not None:
                preview_box.setColor(hou.Color((0.86, 0.78, 0.28)))
                self._set_network_box_label(preview_box, "USD Preview Shader")
                for node in preview_nodes:
                    preview_box.addItem(node)
                self._fit_network_box(preview_box)

    def _wire_material_builder_output(
        self, builder: hou.Node, source_material: hou.Node
    ) -> None:
        # Typical pxrmaterialbuilder includes one or more output nodes.
        wired = False
        for output in self._builder_outputs(builder):
            if output.path() == source_material.path():
                continue
            try:
                output.setInput(0, source_material, 0)
                wired = True
                # Keep wiring all outputs we find so legacy and current outputs stay aligned.
            except (hou.OperationFailed, hou.InvalidInput):
                continue
        if not wired:
            log.warning(
                "Could not find builder output node for %s; material may not export correctly",
                builder.path(),
            )

    def _builder_outputs(self, builder: hou.Node) -> list[hou.Node]:
        outputs: list[hou.Node] = []
        seen: set[str] = set()

        for output_name in _BUILDER_OUTPUT_NODE_NAMES:
            output = builder.node(output_name)
            if output is None:
                continue
            outputs.append(output)
            seen.add(output.path())

        # Fallback heuristic by node type/name.
        for child in builder.children():
            tname = child.type().name().lower()
            nname = child.name().lower()
            if (
                "output" not in tname
                and "suboutput" not in tname
                and "output" not in nname
            ):
                continue
            if child.path() in seen:
                continue
            outputs.append(child)
            seen.add(child.path())

        return outputs

    def _prune_material_builder(self, builder: hou.Node) -> None:
        outputs = {node.path() for node in self._builder_outputs(builder)}
        for child in list(builder.children()):
            if child.path() in outputs:
                continue
            try:
                child.destroy()
            except hou.OperationFailed:
                log.debug("Could not prune default builder node: %s", child.path())
            except Exception:
                log.debug("Could not prune default builder node: %s", child.path())

        if not hasattr(builder, "networkBoxes"):
            return
        for net_box in builder.networkBoxes():
            try:
                net_box.destroy()
            except Exception:
                continue

    @staticmethod
    def _disconnect_inputs(node: hou.Node) -> None:
        for connection in node.inputConnections():
            try:
                node.setInput(connection.inputIndex(), None)
            except (hou.OperationFailed, hou.InvalidInput):
                continue

    def _create_preview_texture(
        self,
        parent: hou.Node,
        tex_set_id: str,
        token: str,
        texture_path: str | None,
        y: int,
        *,
        color: bool,
    ) -> hou.Node | None:
        if not texture_path:
            return None
        node = self._create_first_supported_node(
            parent,
            ("usduvtexture::2.0", "usduvtexture"),
            f"{token}_{tex_set_id}_PreviewTex",
        )
        if node is None:
            log.warning(
                "USD UV Texture node type unavailable; skipping %s preview map", token
            )
            return None
        node.setPosition(hou.Vector2(6, y))

        self._set_parm_if_exists(node, "file", texture_path)
        self._set_parm_if_exists(node, "filename", texture_path)
        self._set_parm_if_exists(node, "sourceColorSpace", "sRGB" if color else "raw")
        self._set_parm_if_exists(node, "sourcecolorspace", "sRGB" if color else "raw")
        return node

    def _set_texture_filename(
        self, node: hou.Node, path: str | None, *, is_color: bool
    ) -> None:
        if not path:
            return
        self._set_parm_if_exists(node, "filename", path)
        if is_color:
            self._set_parm_if_exists(node, "filename_colorspace", "srgb_texture")

    @staticmethod
    def _set_parm_if_exists(node: hou.Node, parm_name: str, value) -> None:
        parm = node.parm(parm_name)
        if parm is None:
            return
        parm.set(value)

    def _connect_named(
        self,
        dest: hou.Node,
        input_name: str,
        src: hou.Node,
        output_names: tuple[str, ...],
    ) -> bool:
        for output_name in output_names:
            try:
                dest.setNamedInput(input_name, src, output_name)
                return True
            except hou.OperationFailed:
                continue
        return False

    def _create_first_supported_node(
        self, parent: hou.Node, type_names: tuple[str, ...], name: str
    ) -> hou.Node | None:
        for node_type in type_names:
            try:
                return self._create_node(parent, node_type, name)
            except hou.OperationFailed:
                continue
        return None

    def _create_node(self, parent: hou.Node, node_type: str, name: str) -> hou.Node:
        node = parent.createNode(node_type)
        node.setName(name, unique_name=True)
        node.setUserData(_AUTO_TAG_KEY, _AUTO_TAG_VALUE)
        return node

    def _create_network_box(self, parent: hou.Node, name: str):
        try:
            net_box = parent.createNetworkBox()
        except Exception:
            return None
        net_box.setName(name, unique_name=True)
        try:
            cast(Any, net_box).setUserData(_AUTO_TAG_KEY, _AUTO_TAG_VALUE)
        except Exception:
            pass
        return net_box

    @staticmethod
    def _fit_network_box(net_box) -> None:
        try:
            net_box.fitAroundContents()
        except Exception:
            pass

    @staticmethod
    def _set_network_box_label(net_box, label: str) -> None:
        if hasattr(net_box, "setComment"):
            try:
                net_box.setComment(label)
                return
            except Exception:
                pass
        if hasattr(net_box, "setLabel"):
            try:
                net_box.setLabel(label)
            except Exception:
                pass

    @staticmethod
    def _set_material_flag(node: hou.Node, state: bool) -> None:
        try:
            if hasattr(node, "setMaterialFlag"):
                node.setMaterialFlag(state)
        except hou.OperationFailed:
            pass


class MatlibManager:
    _conn: DB
    _bound_node: hou.LopNode | None

    def __init__(
        self, node: hou.LopNode | None = None, *, init_defaults: bool = False
    ) -> None:
        self._conn = DB.Get(DB_Config)
        self._bound_node = node
        if node and init_defaults:
            try:
                self._init_hda(node)
            except Exception:
                log.exception("Failed to initialize MatLib defaults on %s", node.path())

    @property
    def node(self) -> hou.LopNode:
        if self._bound_node:
            return self._bound_node
        node = hou.node("./")
        assert isinstance(node, hou.LopNode)
        return node

    @property
    def _asset(self) -> Asset:
        asset_name = str(hou.contextOption("ASSET"))
        return self._conn.get_asset_by_name(asset_name)

    def _get_asset_or_none(self) -> Asset | None:
        try:
            return self._asset
        except Exception:
            log.exception("Failed to resolve ASSET context option for MatLib")
            return None

    @property
    def _hip(self) -> Path:
        return Path(hou.hscriptStringExpression("$HIP"))

    @property
    def geo_variant_name(self) -> str:
        geo_var_name = self.node.parm("geo_var")
        if geo_var_name is None:
            return "main"
        return geo_var_name.evalAsString().strip() or "main"

    @property
    def mat_variant_name(self) -> str:
        mat_var_name = self.node.parm("mat_var")
        if mat_var_name is None:
            return _NO_TEXTURES
        return mat_var_name.evalAsString().strip() or _NO_TEXTURES

    def _init_hda(self, node: hou.LopNode) -> None:
        self._update_default_mat_var(node=node)
        self._update_default_geo_var(node=node)

    def _update_default_geo_var(self, node: hou.LopNode | None = None) -> None:
        curr_node = node or self.node
        geo_var = curr_node.parm("geo_var")
        if geo_var is None:
            return

        asset = self._get_asset_or_none()
        variants = (
            sorted((v for v in asset.geometry_variants if v), key=str.casefold)
            if asset
            else []
        )
        geo_var.set(variants[0] if variants else "main")

    def _update_default_mat_var(self, node: hou.LopNode | None = None) -> None:
        curr_node = node or self.node
        mat_var = curr_node.parm("mat_var")
        if mat_var is None:
            return

        asset = self._get_asset_or_none()
        variants = (
            sorted((v for v in asset.material_variants if v), key=str.casefold)
            if asset
            else []
        )
        mat_var.set(variants[0] if variants else _NO_TEXTURES)

    def _find_material_library(
        self, node: hou.LopNode | None = None
    ) -> hou.Node | None:
        root = node or self.node
        by_name = root.node(f"./{_MATLIB_NAME}")
        if by_name:
            return by_name

        for child in root.children():
            if child.type().name() == "materiallibrary":
                return child
        return None

    @staticmethod
    def _configure_material_library(
        matlib: hou.Node, *, geo_variant: str, mat_variant: str
    ) -> None:
        # Component Material expects materials under
        # /ASSET/mtl/g_<geoVariant>/v_<matVariant>/MAT_<texset>.
        material_prefix = variants.material_scope_path(
            mat_variant, geo_variant=geo_variant
        )
        for parm_name, value in (
            ("matpathprefix", material_prefix),
            ("materialpathprefix", material_prefix),
            ("matnodepattern", "MAT_*"),
        ):
            parm = matlib.parm(parm_name)
            if parm is not None:
                parm.set(value)

    def _build_preview_toggle(self, node: hou.LopNode) -> bool:
        parm = node.parm("build_usd_preview")
        if parm is None:
            return True
        return bool(parm.evalAsInt())

    def _auto_rebuild_enabled(self, node: hou.LopNode) -> bool:
        parm = node.parm("auto_rebuild")
        if parm is None:
            return False
        return bool(parm.evalAsInt())

    def get_geo_variant_list(self) -> list[str]:
        asset = self._get_asset_or_none()
        variants = (
            sorted((v for v in asset.geometry_variants if v), key=str.casefold)
            if asset
            else []
        )
        if not variants:
            variants = ["main"]
        return _asset_menu_format(variants)

    def get_mat_variant_list(self) -> list[str]:
        asset = self._get_asset_or_none()
        variants = (
            sorted((v for v in asset.material_variants if v), key=str.casefold)
            if asset
            else []
        )
        if not variants:
            variants = [_NO_TEXTURES]
        return _asset_menu_format(variants)

    def on_variant_changed(self, node: hou.LopNode | None = None) -> None:
        curr_node = node or self.node
        if self._auto_rebuild_enabled(curr_node):
            self.rebuild(node=curr_node)

    def rebuild(self, node: hou.LopNode | None = None) -> None:
        curr_node = node or self.node
        matlib = self._find_material_library(curr_node)
        if matlib is None:
            log.error("No materiallibrary node found inside %s", curr_node.path())
            return
        self._configure_material_library(
            matlib,
            geo_variant=self.geo_variant_name,
            mat_variant=self.mat_variant_name,
        )

        discovery = MatlibDiscovery(
            self._hip, self.geo_variant_name, self.mat_variant_name
        )
        layers = discovery.discover_layers()
        spec = MatlibSpecBuilder(self._hip).build(
            geo_variant=self.geo_variant_name,
            mat_variant=self.mat_variant_name,
            layers=layers,
        )

        builder = MatlibNodeBuilder(matlib)
        builder.rebuild(spec, build_preview=self._build_preview_toggle(curr_node))

    def create_matnet(
        self,
        houdini_filepath: str | None = None,
        node: hou.LopNode | None = None,
    ) -> None:
        self.rebuild(node=node)


def matlib_on_created(node: hou.LopNode) -> None:
    MatlibManager(node=node, init_defaults=True)


def matlib_geo_variant_menu(node: hou.LopNode) -> list[str]:
    return MatlibManager(node=node).get_geo_variant_list()


def matlib_mat_variant_menu(node: hou.LopNode) -> list[str]:
    return MatlibManager(node=node).get_mat_variant_list()


def matlib_on_variant_changed(node: hou.LopNode) -> None:
    MatlibManager(node=node).on_variant_changed(node)


def matlib_rebuild(node: hou.LopNode) -> None:
    MatlibManager(node=node).rebuild(node)


class MatlibErrorChecker:
    @staticmethod
    def CheckFilepathsRelative(matlib: hou.Node) -> int:
        """Returns 1 if there are any absolute filepaths in generated textures."""
        nodes: list[hou.Node] = [matlib]
        try:
            nodes.extend(matlib.allSubChildren())
        except Exception:
            nodes.extend(matlib.children())

        for node in nodes:
            if (fn := node.parm("filename")) is not None:
                if not fn.unexpandedString().startswith("$"):
                    return 1
        return 0
