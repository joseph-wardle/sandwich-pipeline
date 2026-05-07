import json
import re
from typing import Iterable, Iterator, Literal

from maya import cmds
from maya.api.OpenMaya import MDagPath, MFnDagNode, MItDag, MSelectionList

CONTROLS_SET_NAME = "rig_controllers_grp"
GEO_SET_NAME = "rig_geo_grp"
ROOT_NODE_NAME = "rig"
GEO_GROUP_NAME = "geo"


def get_dag_path(transform: str) -> MDagPath:
    sel: MSelectionList = MSelectionList()
    sel.add(transform)
    dag_path: MDagPath = sel.getDagPath(0)
    return dag_path


def is_visible(object: str) -> bool:
    try:
        dag_path: MDagPath = get_dag_path(object)
    except Exception:
        return False
    return dag_path.isVisible()


def get_evaluation_graph(attributes: Literal["nodes", "plugs", "connections"]):
    return json.loads(
        cmds.dbpeek(
            operation="graph",
            evaluationGraph=True,
            argument=attributes,
            allObjects=True,
        )  # type: ignore
    )


def get_evaluation_manager_nodes() -> list[str]:
    raw_json = get_evaluation_graph("nodes")
    if not raw_json:
        return []
    nodeList = raw_json["nodes"]
    return nodeList


def iter_dag_nodes(dag_iterator: MItDag) -> Iterator[MFnDagNode]:
    while not dag_iterator.isDone():
        current_node = dag_iterator.currentItem()
        dag_fn = MFnDagNode(current_node)
        yield dag_fn
        dag_iterator.next()


MATCH_CONTROLS_REGEX = re.compile(r"(?i)(?<=_)(?:ctrl|cntrl|ctl|control)(?=_?\d*$)")


def get_all_controls_by_name() -> list[str]:
    """
    This tries to return as many transforms that could be controls as possible.
    These should be used later for validating that controls are properly tagged, named, zeroed, etc.
    """
    transforms = cmds.ls(type="transform")
    return [
        transform for transform in transforms if MATCH_CONTROLS_REGEX.search(transform)
    ]


def get_all_visible_meshes() -> set[str]:
    mesh_shapes: list[str] = cmds.ls(type="mesh")
    mesh_transforms: list[str] = cmds.listRelatives(mesh_shapes, parent=True) or []  # type: ignore

    visible_geo = set(geo for geo in mesh_transforms if is_visible(geo))
    return visible_geo


def is_control(transform: str, strict: bool = True) -> bool:
    """Returns True if the given transform is a tagged controller."""
    if strict:
        return cmds.controller(transform, query=True, isController=True)  # type: ignore
    else:
        return cmds.controller(transform, query=True, isController=True) or bool(
            MATCH_CONTROLS_REGEX.search(transform)
        )  # type: ignore


def format_max_items(
    iterable: Iterable, item_name: str = "item(s)", max_items: int = 10
):
    displayed_items = []
    count = 0

    # Try to get length if possible
    try:
        total_len = len(iterable)  # works for lists, tuples, sets # type: ignore
    except TypeError:
        total_len = None

    it: Iterator = iter(iterable)
    try:
        while count < max_items:
            displayed_items.append(next(it))
            count += 1
    except StopIteration:
        # Less than max_items
        return f"[{', '.join(map(str, displayed_items))}]"

    # Check if there are more items without consuming all
    try:
        next(it)
        more = True
    except StopIteration:
        more = False

    result = f"[{', '.join(map(str, displayed_items))}"
    if more:
        result += ", ...]"
    else:
        result += "]"

    if total_len is not None:
        result += f" {total_len} {item_name}"
    return result
