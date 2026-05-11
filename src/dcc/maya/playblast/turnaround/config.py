from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import maya.cmds as mc

from core.playblast import FFmpegPreset, Playblaster

log = logging.getLogger(__name__)

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FRAMES_PER_PASS = 96
DEFAULT_FOCAL_LENGTH = 50.0
DEFAULT_CAMERA_PADDING = 1.25


@dataclass(frozen=True)
class TurnaroundReviewRoots:
    """Resolved roots to display in the turnaround capture."""

    roots: tuple[str, ...]
    source_label: str

    @property
    def summary(self) -> str:
        if not self.roots:
            return "No review roots found."

        if len(self.roots) == 1:
            return _short_name(self.roots[0])

        first_name = _short_name(self.roots[0])
        return f"{first_name} + {len(self.roots) - 1} more"


@dataclass(frozen=True)
class TurnaroundPlayblastConfig:
    """All settings required to export an asset turnaround movie."""

    asset_label: str
    output_paths: dict[FFmpegPreset, list[str | Path]]
    review_roots: tuple[str, ...]
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    frames_per_pass: int = DEFAULT_FRAMES_PER_PASS
    frame_rate: int = Playblaster.fps
    focal_length: float = DEFAULT_FOCAL_LENGTH
    camera_padding: float = DEFAULT_CAMERA_PADDING
    use_default_material: bool = True
    use_shadows: bool = True
    use_anti_aliasing: bool = True
    include_wireframe_pass: bool = True


def resolve_turnaround_review_roots() -> TurnaroundReviewRoots:
    """Resolve review roots from the current Maya selection or visible meshes."""

    selected_transforms = _collapse_to_root_transforms(
        _selected_root_candidates(),
    )
    if selected_transforms:
        return TurnaroundReviewRoots(selected_transforms, "Selection")

    scene_transforms = _collapse_to_root_transforms(_visible_scene_mesh_roots())
    return TurnaroundReviewRoots(scene_transforms, "Visible Geometry")


def _selected_root_candidates() -> tuple[str, ...]:
    selection = mc.ls(selection=True, long=True, objectsOnly=True) or []
    resolved_roots: list[str] = []
    for node in selection:
        transform = _as_transform(node)
        if transform:
            resolved_roots.append(transform)
    return tuple(resolved_roots)


def _visible_scene_mesh_roots() -> tuple[str, ...]:
    scene_roots: list[str] = []
    for mesh in mc.ls(type="mesh", long=True) or []:
        if mc.getAttr(f"{mesh}.intermediateObject"):
            continue
        parent = _first_parent(mesh)
        if not parent or not _is_visible_in_hierarchy(parent):
            continue
        scene_roots.append(parent)
    return tuple(scene_roots)


def _collapse_to_root_transforms(nodes: Iterable[str]) -> tuple[str, ...]:
    candidate_paths_by_uuid: dict[str, str] = {}
    for node in nodes:
        normalized = str(node).strip()
        if not normalized:
            continue

        transform = _as_transform(normalized)
        if not transform:
            continue
        candidate_paths_by_uuid[_node_uuid(transform)] = transform

    candidate_paths = tuple(candidate_paths_by_uuid.values())
    candidate_set = set(candidate_paths)

    collapsed_roots: list[str] = []
    for node in candidate_paths:
        parent = _first_parent(node)
        has_selected_ancestor = False
        while parent:
            if parent in candidate_set:
                has_selected_ancestor = True
                break
            parent = _first_parent(parent)

        if not has_selected_ancestor:
            collapsed_roots.append(node)

    return tuple(collapsed_roots)


def _as_transform(node: str) -> str | None:
    if not mc.objExists(node):
        return None

    if mc.nodeType(node) == "transform":
        return str(mc.ls(node, long=True)[0])

    parent = _first_parent(node)
    if parent:
        return parent
    return None


def _first_parent(node: str) -> str | None:
    parents = mc.listRelatives(node, parent=True, fullPath=True) or []
    if not parents:
        return None
    return str(parents[0])


def _is_visible_in_hierarchy(node: str) -> bool:
    current = node
    while current:
        try:
            if not mc.getAttr(f"{current}.visibility"):
                return False
        except (RuntimeError, ValueError):
            return False

        parent = _first_parent(current)
        if parent == current:
            break
        current = parent
    return True


def _short_name(node: str) -> str:
    return str(node).split("|")[-1]


def _node_uuid(node: str) -> str:
    uuids = mc.ls(node, uuid=True) or []
    if not uuids:
        raise ValueError(f"Could not resolve UUID for node '{node}'.")
    return str(uuids[0])


__all__ = [
    "DEFAULT_CAMERA_PADDING",
    "DEFAULT_FOCAL_LENGTH",
    "DEFAULT_FRAMES_PER_PASS",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "TurnaroundPlayblastConfig",
    "TurnaroundReviewRoots",
    "resolve_turnaround_review_roots",
]
