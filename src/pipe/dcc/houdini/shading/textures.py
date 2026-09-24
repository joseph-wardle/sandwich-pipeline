"""Published textures on disk to deterministic material specs.

A published material variant looks like::

    publish/tex/<geo>/<mat>/<layer>/            RenderMan `.tex`
    publish/tex/<geo>/<mat>/<layer>/_preview/   the same maps as 1k `.jpeg` per UDIM
    publish/tex/<geo>/<mat>/<layer>/_src/       Substance exports, render fallback
    publish/tex/<geo>/<mat>/<layer>/mat.json    the publish's own manifest

Both surfaces consume the same four maps and the same `st` coordinates; only
the file format differs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pipe.core.asset.paths import (
    PUBLISH_TEXTURES_PREVIEW_DIRNAME,
    PUBLISH_TEXTURES_SOURCE_DIRNAME,
)
from pipe.core.struct.material import MaterialInfo

from .variants import to_hip_expression

log = logging.getLogger(__name__)

MANIFEST_NAME = "mat.json"

MAPS: tuple[str, ...] = ("BaseColor", "Metallic", "SpecularRoughness", "Normal")

# File formats each surface prefers, best first.
_RENDER_EXTENSIONS = ("tex", "exr", "png")
_PREVIEW_EXTENSIONS = ("jpeg", "jpg")


@dataclass(frozen=True)
class LayerSpec:
    """One layer of a material: map name to `$HIP`-relative texture path."""

    name: str
    render_maps: dict[str, str]


@dataclass(frozen=True)
class MaterialSpec:
    """Everything needed to build one material for one texture set."""

    texture_set: str
    layers: tuple[LayerSpec, ...]
    preview_maps: dict[str, str]


_MAP_ALTERNATION = "|".join(MAPS)
_TEX_FILE_RE = re.compile(
    rf"^(?P<tex_set>.+?)_(?P<map>{_MAP_ALTERNATION})(?:_[^.]+)?"
    rf"(?:\.(?P<udim>\d{{4}}))?\.(?P<ext>[A-Za-z0-9]+)$"
)
_UDIM_RE = re.compile(r"\.(?P<udim>\d{4})(?=\.[^.]+$)")


@dataclass(frozen=True)
class _TextureFile:
    tex_set: str
    map_name: str
    path: Path
    extension: str
    udim: str | None
    # 0 for a file published for its role, 1 for the `_src` render fallback.
    priority: int


@dataclass(frozen=True)
class _Layer:
    name: str
    declared_tex_sets: frozenset[str]
    render: tuple[_TextureFile, ...]
    preview: tuple[_TextureFile, ...]


def published_materials(tex_root: Path, *, hip_root: Path) -> tuple[MaterialSpec, ...]:
    """Materials published under `tex_root`, ordered by texture-set name."""
    if not tex_root.is_dir():
        log.warning("Texture publish path does not exist: %s", tex_root)
        return ()

    layers = tuple(_read_layer(directory) for directory in _subdirectories(tex_root))
    tex_sets = {name for layer in layers for name in _tex_set_names(layer)}

    materials: list[MaterialSpec] = []
    for tex_set in sorted(tex_sets, key=str.casefold):
        layer_specs = tuple(
            LayerSpec(name=layer.name, render_maps=render_maps)
            for layer in layers
            if (
                render_maps := _chosen_paths(
                    layer.render, tex_set, _RENDER_EXTENSIONS, hip_root
                )
            )
        )
        if not layer_specs:
            log.warning(
                "Texture set '%s' has no usable render maps under %s; published "
                "filenames must name one of %s.",
                tex_set,
                tex_root,
                list(MAPS),
            )
            continue

        preview_maps: dict[str, str] = {}
        for layer in layers:
            # One preview surface per material, so later layers deliberately win.
            preview_maps.update(
                _chosen_paths(layer.preview, tex_set, _PREVIEW_EXTENSIONS, hip_root)
            )

        materials.append(MaterialSpec(tex_set, layer_specs, preview_maps))
    return tuple(materials)


def _read_layer(layer_dir: Path) -> _Layer:
    # No `_src` fallback for previews: a missing jpeg should leave the input
    # unwired rather than hand the viewport a full-resolution 16-bit png.
    return _Layer(
        name=layer_dir.name,
        declared_tex_sets=_declared_tex_sets(layer_dir / MANIFEST_NAME),
        render=_texture_files(layer_dir, priority=0)
        + _texture_files(layer_dir / PUBLISH_TEXTURES_SOURCE_DIRNAME, priority=1),
        preview=_texture_files(
            layer_dir / PUBLISH_TEXTURES_PREVIEW_DIRNAME, priority=0
        ),
    )


def _declared_tex_sets(manifest: Path) -> frozenset[str]:
    """Texture sets the publish itself claims to contain."""
    if not manifest.is_file():
        return frozenset()
    try:
        info = MaterialInfo.from_json(manifest.read_text(encoding="utf-8"))
    except Exception:
        # Unreadable file, bad JSON, or a schema cattrs cannot structure. None of
        # those should stop a rebuild, because filenames already carry the names.
        log.exception("Ignoring unreadable %s", manifest)
        return frozenset()
    return frozenset(info.tex_sets)


def _texture_files(directory: Path, *, priority: int) -> tuple[_TextureFile, ...]:
    if not directory.is_dir():
        return ()
    # Anything not a subdirectory, so a published-but-unreadable file still
    # reaches the graph as a broken texture instead of vanishing as unpublished.
    parsed = (
        _parse_texture_file(path, priority)
        for path in _sorted_entries(directory)
        if not path.is_dir()
    )
    return tuple(texture for texture in parsed if texture is not None)


def _parse_texture_file(path: Path, priority: int) -> _TextureFile | None:
    match = _TEX_FILE_RE.match(path.name)
    if match is None:
        return None
    tex_set = match.group("tex_set").strip()
    if not tex_set:
        return None
    return _TextureFile(
        tex_set=tex_set,
        map_name=match.group("map"),
        path=path,
        extension=path.suffix.lstrip(".").lower(),
        udim=match.group("udim"),
        priority=priority,
    )


def _chosen_paths(
    files: tuple[_TextureFile, ...],
    tex_set: str,
    extensions: tuple[str, ...],
    hip_root: Path,
) -> dict[str, str]:
    """Best file per map for one texture set, as `$HIP` expressions."""
    best: dict[str, _TextureFile] = {}
    for texture in files:
        if texture.tex_set != tex_set:
            continue
        current = best.get(texture.map_name)
        if current is None or _rank(texture, extensions) < _rank(current, extensions):
            best[texture.map_name] = texture
    return {
        name: _texture_expression(best[name], hip_root) for name in MAPS if name in best
    }


def _rank(texture: _TextureFile, extensions: tuple[str, ...]) -> tuple[int, int, str]:
    try:
        extension = extensions.index(texture.extension)
    except ValueError:
        extension = len(extensions)
    return texture.priority, extension, texture.path.name.casefold()


def _texture_expression(texture: _TextureFile, hip_root: Path) -> str:
    expression = to_hip_expression(texture.path, hip_root=hip_root)
    return _UDIM_RE.sub(".<UDIM>", expression) if texture.udim else expression


def _tex_set_names(layer: _Layer) -> set[str]:
    found = (texture.tex_set for texture in (*layer.render, *layer.preview))
    return {*layer.declared_tex_sets, *found}


def _sorted_entries(directory: Path) -> list[Path]:
    return sorted(directory.iterdir(), key=lambda path: path.name.casefold())


def _subdirectories(root: Path) -> list[Path]:
    return [
        path
        for path in _sorted_entries(root)
        if path.is_dir() and not path.name.startswith(".")
    ]


__all__ = [
    "MAPS",
    "LayerSpec",
    "MaterialSpec",
    "published_materials",
]
