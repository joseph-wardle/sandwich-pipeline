from maya import cmds

from .. import RigBuildTest
from ..common import ROOT_NODE_NAME, format_max_items

DEFAULT_NODES = {"persp", "top", "front", "side"}


def _get_top_level_nodes() -> list[str]:
    top_level_nodes = cmds.ls(assemblies=True)
    non_default_top_level_nodes = [
        node for node in top_level_nodes if node not in DEFAULT_NODES
    ]
    return non_default_top_level_nodes


class TestSingleHierachy(RigBuildTest):
    """
    Checks that the scene consists of only a single rig hierarchy.
    (Exactly one root node).
    """

    def __init__(self):
        super().__init__("Single hierarchy")

    def run(self) -> bool:
        top_level_nodes = _get_top_level_nodes()
        if not top_level_nodes:
            self.log_warn("Scene has no root node")
            return False
        if len(top_level_nodes) > 1:
            self.log_warn(
                f"Scene has more than one root node: {format_max_items(top_level_nodes, 'node(s)')}"
            )
            return False
        self.log_success()
        return True


class TestRootNodeNaming(RigBuildTest):
    """
    Checks that the rig root node is named properly.
    """

    def __init__(self):
        super().__init__("Root node naming")

    def run(self) -> bool:
        top_level_nodes = _get_top_level_nodes()
        if not top_level_nodes:
            self.log_warn("Scene has no root node")
            return False
        if len(top_level_nodes) > 1 and ROOT_NODE_NAME not in top_level_nodes:
            self.log_warn(
                f'Scene has more than one root node and none where named "{ROOT_NODE_NAME}"'
            )
            return False
        root_node = top_level_nodes[0]
        if root_node != ROOT_NODE_NAME:
            self.log_warn(
                f'Root node had incorrect naming: "{root_node}" instead of "{ROOT_NODE_NAME}"'
            )
            return False
        else:
            self.log_success()
            return True
