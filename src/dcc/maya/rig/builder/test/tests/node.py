from maya import cmds

from .. import RigBuildTest
from ..common import format_max_items


class TestUnknownNodes(RigBuildTest):
    """
    Checks that the scene has no nodes of an unknown type (due to a missing plugin or otherwise).
    """

    def __init__(self):
        super().__init__("No unknown nodes")

    def run(self) -> bool:
        unknown_nodes = cmds.ls(type="unknown")
        if unknown_nodes:
            self.log_warn(
                f"Scene has unknown nodes: {format_max_items(unknown_nodes, 'node(s)')}"
            )
            return False
        else:
            self.log_success()
            return True
