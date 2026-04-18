from maya import cmds

from .. import RigBuildTest
from ..common import (
    format_max_items,
    is_visible,
)

HIDDEN_NODE_TYPES = {"ikHandle", "locator", "clusterHandle", "follicle", "lattice"}


class TestHiddenRigNodes(RigBuildTest):
    """
    Checks that the scene has no visible rig nodes (ikHandles, locators, etc.).
    These nodes don't hide in the viewport when disabling NURBS curves with alt+1 which is annoying for animators.
    """

    def __init__(self):
        super().__init__("No visible rig nodes")

    def run(self) -> bool:
        rig_nodes: list[tuple[str, str]] = []
        for node_type in HIDDEN_NODE_TYPES:
            shapes = cmds.ls(exactType=node_type) or []
            rig_nodes.extend((shape, node_type) for shape in shapes)
        rig_nodes_set: set[tuple[str, str]] = set(rig_nodes)
        problem_rig_nodes: list[str] = [
            f"{rig_node[0]}: {rig_node[1]}"
            for rig_node in rig_nodes_set
            if is_visible(rig_node[0])
        ]

        if problem_rig_nodes:
            self.log_warn(
                f"Scene has visible rig nodes: {format_max_items(problem_rig_nodes, 'node(s)')}"
            )
            return False
        else:
            self.log_success()
            return True
