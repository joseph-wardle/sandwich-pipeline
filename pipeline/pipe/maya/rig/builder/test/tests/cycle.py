from typing import FrozenSet, Iterable, Sequence

from maya import cmds

from .. import RigBuildTest
from ..common import get_evaluation_manager_nodes


def _format_clusters_for_log(clusters: Iterable[Sequence[str]]):
    cluster_sizes_and_names = ((len(cluster), cluster[0]) for cluster in clusters)
    cluster_log_strings: list[str] = [
        f"{cluster_data[1]}: {cluster_data[0]} nodes"
        for cluster_data in sorted(
            cluster_sizes_and_names, key=lambda x: x[0], reverse=True
        )
    ]
    return cluster_log_strings


class TestLargeCyclesEM(RigBuildTest):
    """
    Checks that the scene has no large evaluation manager cycles. Currently the threshold is 25 nodes.

    We don't have this on by default since controls that are hooked to an IK chain
    that have an attribute for IK/FK switch will be counted as a cycle.
    It's dumb that Maya isn't smart enough to know that only certain attributes on a node
    are dependent on their DAG parent, but oh well, suck it up.
    """

    CYCLE_THRESHOLD = 25

    def __init__(self):
        super().__init__("No large cycles (EM)")

    def run(self) -> bool:
        # invalidate the graph so we can query it after a build.
        cmds.evaluationManager(invalidate=True)
        evaluation_nodes: list[str] = get_evaluation_manager_nodes()

        # Evaluation Manager Cycles
        processed_nodes: set[str] = set()
        large_cycle_clusters: list[list[str]] = []
        unique_clusters: set[FrozenSet[str]] = set()
        for node in evaluation_nodes:
            if node in processed_nodes:
                continue
            cycle_cluster: list[str] = cmds.evaluationManager(cycleCluster=node)  # type: ignore
            if cycle_cluster:
                processed_nodes.update(cycle_cluster)
                if len(cycle_cluster) > self.CYCLE_THRESHOLD:
                    cluster_set = frozenset(cycle_cluster)
                    if cluster_set in unique_clusters:
                        continue
                    large_cycle_clusters.append(cycle_cluster)
                    unique_clusters.add(cluster_set)
            else:
                processed_nodes.add(node)

        if large_cycle_clusters:
            cluster_log_strings: list[str] = _format_clusters_for_log(
                large_cycle_clusters
            )
            formatted_clusters = "\n".join(cluster_log_strings)
            self.log_warn(f"Scene has large EM cluster(s): {formatted_clusters}")
            return False
        else:
            self.log_success()
            return True


def _get_cycles_from_nodes(nodes: Iterable[str], threshold: int = 0) -> list[list[str]]:
    processed_nodes: set[str] = set()
    large_cycle_clusters: list[list[str]] = []
    unique_clusters: set[FrozenSet[str]] = set()
    for node in nodes:
        if node in processed_nodes:
            continue
        cycle_cluster: list[str] = cmds.cycleCheck(node, list=True)  # type: ignore
        if cycle_cluster:
            processed_nodes.update(cycle_cluster)
            if len(cycle_cluster) > threshold:
                cluster_set = frozenset(cycle_cluster)
                if cluster_set in unique_clusters:
                    continue
                large_cycle_clusters.append(cycle_cluster)
                unique_clusters.add(cluster_set)
        else:
            processed_nodes.add(node)
    return large_cycle_clusters


def _run_dg_cycle_test(test_object: RigBuildTest, threshold: int = 0):
    # Dependency Graph Cycle
    dg_cycle_nodes: list[str] = cmds.cycleCheck(all=True, list=True) or []  # type: ignore

    large_cycle_clusters = _get_cycles_from_nodes(dg_cycle_nodes, threshold=threshold)
    if large_cycle_clusters:
        cluster_log_strings: list[str] = _format_clusters_for_log(large_cycle_clusters)
        formatted_clusters = "\n".join(cluster_log_strings)
        test_object.log_warn(f"Scene has large DG cluster(s): {formatted_clusters}")
        return False
    else:
        test_object.log_success()
        return True


class TestLargeCyclesDG(RigBuildTest):
    """
    Checks that the scene has no large dependency graph cycles. Currently the threshold is 10 nodes.
    """

    CYCLE_THRESHOLD = 10

    def __init__(self):
        super().__init__("No large cycles (DG)")

    def run(self):
        return _run_dg_cycle_test(self, self.CYCLE_THRESHOLD)


class TestCyclesDG(RigBuildTest):
    """
    Checks that the scene has no dependency graph cycles.
    """

    def __init__(self):
        super().__init__("No cycles (DG)")

    def run(self):
        return _run_dg_cycle_test(self, 0)
