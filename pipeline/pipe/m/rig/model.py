import re
import unicodedata
from pathlib import Path

from maya import cmds


def import_model_usd(filepath: Path) -> list[str]:
    imported_top_level_prims = cmds.mayaUSDImport(  # type: ignore
        file=str(filepath), primPath="/", preferredMaterial="openPBRSurface"
    )
    return imported_top_level_prims


def group_top_level_nodes(
    group_name: str = "geo", nodes: list[str] | None = None
) -> str:
    top_level_nodes: list[str]
    if nodes is not None:
        top_level_nodes = cmds.ls(nodes, assemblies=True)  # type: ignore
    else:
        top_level_nodes = cmds.ls(assemblies=True)  # type: ignore

    if not top_level_nodes:
        raise RuntimeError("No transforms found in imported model!")

    # Case 1: exactly one root transform (a group)
    if len(top_level_nodes) == 1:
        top_level_node = top_level_nodes[0]
        if cmds.nodeType(top_level_node) == "transform" and not cmds.listRelatives(
            top_level_node, shapes=True
        ):
            geo_grp = top_level_node
        else:
            geo_grp = cmds.group(top_level_node, name=group_name, world=True)
        if geo_grp != group_name:
            geo_grp = cmds.rename(geo_grp, group_name)
    else:
        # Case 2: multiple top level transforms (we need to group them)
        geo_grp = cmds.group(top_level_nodes, name=group_name, world=True)  # type: ignore

    return geo_grp


NON_ALPHANUMERIC_REGEX = re.compile(r"[^a-z0-9_]")
MODEL_SUFFIX_REGEX = re.compile(r"(?i)_(?:mdl|geo|model|mesh)(?=$)")
SPECIAL_SUFFIX_REGEX = re.compile(r"(?i)_(?:sim|proxy)(?=$)")


def normalize_name(name: str) -> str:
    """Normalize a name for renaming rig geo.

    Current steps:
    - normalizes the unicode + encodes to ascii: aあä > aa
    - lower-case the string
    - replace spaces with underscores
    - removes all non alpha_numeric characters
    - removes any model suffixes
    """
    if not name:
        return ""
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    normalized_name = ascii_name.strip().lower().replace(" ", "_")
    normalized_name = re.sub(NON_ALPHANUMERIC_REGEX, "", normalized_name)
    normalized_name = re.sub(MODEL_SUFFIX_REGEX, "", normalized_name)
    return normalized_name


def add_model_suffix(name: str, suffix: str = "geo"):
    if re.search(SPECIAL_SUFFIX_REGEX, name) is not None:
        return name
    return f"{name}_{suffix}"


def _merge_group(group: str) -> str | None:
    group_parents = cmds.listRelatives(group, parent=True)
    group_parent: str | None
    group_parent = group_parents[0] if group_parents else None

    # capture original position index
    sibling_index = None
    if group_parent:
        siblings = (
            cmds.listRelatives(group_parent, children=True, type="transform") or []
        )
        if group in siblings:
            sibling_index = siblings.index(group)

    child_transforms = (
        cmds.listRelatives(group, children=True, shapes=False, type="transform") or []
    )

    # Delete empty groups
    if not child_transforms:
        if cmds.listRelatives(group, children=True, shapes=True):
            return group
        cmds.delete(group)
        return None

    # Only one mesh → just rename/reparent it
    if len(child_transforms) == 1:
        transform = child_transforms[0]
        if group_parent:
            cmds.parent(transform, group_parent)
        else:
            cmds.parent(transform, world=True)
        return cmds.rename(transform, group)

    merged_and_merge_node: list[str] = cmds.polyUnite(child_transforms)  # type: ignore
    merged: str = merged_and_merge_node[0]
    if group_parent is not None:
        cmds.parent(merged, group_parent)
    else:
        cmds.parent(merged, world=True)
    cmds.delete(merged, constructionHistory=True)
    final_name = cmds.rename(merged, group)

    if sibling_index is not None:
        cmds.reorder(final_name, front=True)
        cmds.reorder(final_name, relative=sibling_index)

    return final_name


def merge_by_groups(geo_group: str, model_suffix: str = "geo") -> list[str]:
    child_transforms = cmds.listRelatives(geo_group, children=True, shapes=False)
    if len(child_transforms) == 0:
        return []
    merged_transforms: list[str] = []
    for child in child_transforms:
        merged = _merge_group(child)
        if merged is None:
            continue
        normalized_name = normalize_name(merged)
        name_with_suffix = add_model_suffix(normalized_name)
        final_name = cmds.rename(merged, name_with_suffix)
        merged_transforms.append(final_name)
    return merged_transforms


def import_and_merge(filepath: Path) -> list[str]:
    imported = import_model_usd(filepath)
    group = group_top_level_nodes(nodes=imported)
    return merge_by_groups(group)
