from maya import cmds

from .. import RigBuildTest
from ..common import format_max_items

DEFAULT_NAMESPACES = {"UI", "shared"}


class TestNamespaces(RigBuildTest):
    """
    Checks that the scene has no nodes with leftover namespaces.
    These clutter the hierarchy and can cause issues with animation publish.
    """

    def __init__(self):
        super().__init__("No namespaces")

    def run(self) -> bool:
        namespaces = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
        bad_namespaces: list[str] = [
            ns for ns in namespaces if ns not in DEFAULT_NAMESPACES
        ]
        if bad_namespaces:
            namespace_nodes: list[str] = []
            for namespace in bad_namespaces:
                nodes = (
                    cmds.namespaceInfo(
                        namespace,
                        listNamespace=True,
                    )
                    or []
                )
                namespace_nodes.extend(nodes)
            self.log_warn(
                f"Scene has nodes in namespaces: {format_max_items(namespace_nodes, 'node(s)')}"
            )
            return False
        else:
            self.log_success()
            return True
