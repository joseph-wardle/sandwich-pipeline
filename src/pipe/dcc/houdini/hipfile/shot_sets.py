"""The `sets` Reference LOP that brings a shot's sets into its stage (ADR-0024)."""

from __future__ import annotations

from typing import cast

import hou

from pipe.core.sets import current_layer_path
from pipe.core.shotgrid import Set
from pipe.core.util.paths import get_production_path

SETS_NODE_NAME = "sets"
SETS_PRIM = "/sets"


def create_sets_node(stage: hou.Node) -> hou.LopNode:
    node = cast(hou.LopNode, stage.createNode("reference::2.0", SETS_NODE_NAME))
    node.setParms({"num_files": 0})
    return node


def sync_sets(node: hou.Node, sets: list[Set]) -> list[Set]:
    """Add an entry for each set `node` doesn't have yet, and return those sets.

    Never removes an entry. An entry is the set's if it targets `/sets/<name>`,
    enabled or not, so disabling an entry is how an artist drops a set.
    """
    present = _prim_paths(node)
    added = [set for set in sets if _prim_path(set) not in present]
    for set in added:
        _add_entry(node, set)
    return added


def _prim_paths(node: hou.Node) -> set[str]:
    return {
        cast(str, node.evalParm(f"primpath{i}")) for i in range(1, _count(node) + 1)
    }


def _add_entry(node: hou.Node, set: Set) -> None:
    index = _count(node) + 1
    node.setParms({"num_files": index})
    layer = current_layer_path(set.name).relative_to(get_production_path())
    node.setParms(
        {
            f"primpath{index}": _prim_path(set),
            f"reftype{index}": "payload",
            f"filepath{index}": f"$JOB/{layer}",
            f"filerefprim{index}": "defaultPrim",
        }
    )


def _count(node: hou.Node) -> int:
    return cast(int, node.evalParm("num_files"))


def _prim_path(set: Set) -> str:
    return f"{SETS_PRIM}/{set.name}"
