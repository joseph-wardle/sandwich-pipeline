"""The rules a model scene's materials must meet before they leave Maya."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from maya import cmds as mc


@dataclass(frozen=True)
class ShaderRule:
    pattern: re.Pattern[str]
    message: str


ILLEGAL_SHADER_RULES: tuple[ShaderRule, ...] = (
    ShaderRule(re.compile("initialShadingGroup"), "No material set"),
    ShaderRule(re.compile(r"\d$"), "Material name with a trailing digit"),
    ShaderRule(re.compile(r"SG$"), 'Material name that ends with "SG"'),
    ShaderRule(
        re.compile(
            r"aiStandardSurface|standardSurface|openPBRSurface|lambert|phong|blinn"
        ),
        "Unnamed material (material name has default shader name in it)",
    ),
)
ILLEGAL_SHADER_TYPES = {"aiStandardSurface", "aiAmbientOcclusion"}


def material_problems(nodes: Iterable[str]) -> list[str]:
    """What is wrong with the materials on the meshes under `nodes`, one line each."""
    failures: dict[str, list[str]] = {}
    for shading_group in sorted(shading_groups(nodes)):
        shaders = cast(
            list[str] | None,
            mc.listConnections(f"{shading_group}.surfaceShader", source=True),
        )
        if shaders:
            shader_type = cast(str, mc.nodeType(shaders[0]))
            if shader_type in ILLEGAL_SHADER_TYPES:
                failures.setdefault("Non-allowed shader type", []).append(
                    f"{shading_group} ({shader_type})"
                )
        for rule in ILLEGAL_SHADER_RULES:
            if rule.pattern.search(shading_group):
                failures.setdefault(rule.message, []).append(shading_group)
    return [f"{message}: {', '.join(items)}" for message, items in failures.items()]


def shading_groups_in_use(names: Iterable[str]) -> list[str]:
    """The shading groups in `names` that some geometry is still assigned to."""
    return [
        name
        for name in names
        if mc.ls(name, type="shadingEngine") and mc.sets(name, query=True)
    ]


def delete_unused_shading_groups(names: Iterable[str]) -> None:
    """Delete the shading groups in `names` that no geometry uses, each with its
    surface shader when nothing else uses that either.

    Deletes scene nodes. A shading group left behind by geometry that has gone
    into USD would make the next pull of that geometry rename its materials.
    """
    names = list(names)
    in_use = set(shading_groups_in_use(names))
    for name in names:
        if name in in_use or not mc.ls(name, type="shadingEngine"):
            continue
        shaders = cast(
            list[str] | None,
            mc.listConnections(f"{name}.surfaceShader", source=True, destination=False),
        )
        mc.delete(name)
        for shader in shaders or []:
            if not mc.listConnections(shader, type="shadingEngine"):
                mc.delete(shader)


def shading_groups(nodes: Iterable[str]) -> set[str]:
    """The shading groups assigned to the meshes under `nodes`."""
    nodes = list(nodes)
    if not nodes:
        return set()
    descendants = cast(
        list[str] | None, mc.listRelatives(*nodes, allDescendents=True, fullPath=True)
    )
    meshes = cast(
        list[str],
        mc.ls(*nodes, *(descendants or []), type="mesh", noIntermediate=True, long=True)
        or [],
    )
    if not meshes:
        return set()
    return set(cast(list[str], mc.listConnections(*meshes, type="shadingEngine") or []))
