from collections import Counter, defaultdict

from maya.api.OpenMaya import MItDag

from .. import RigBuildTest
from ..common import iter_dag_nodes


class TestDuplicateDagNames(RigBuildTest):
    """
    Checks that the scene has no duplicate DAG names (these types of nodes may cause problems for third party tools).
    """

    def __init__(self):
        super().__init__("No duplicate DAG names")

    def run(self) -> bool:
        dag_iterator = MItDag(MItDag.kDepthFirst)
        short_name_counter: Counter[str] = Counter()
        name_to_paths: defaultdict[str, list[str]] = defaultdict(list)
        for dag_fn in iter_dag_nodes(dag_iterator):
            short_name = dag_fn.name()
            full_path = dag_fn.fullPathName()
            short_name_counter[short_name] += 1
            name_to_paths[short_name].append(full_path)

        duplicates: list[list[str]] = [
            name_to_paths[name]
            for name, count in short_name_counter.items()
            if count > 1
        ]
        if duplicates:
            self.log_warn(f"Scene has duplicate DAG node names: {duplicates}")
            return False
        else:
            self.log_success()
            return True
