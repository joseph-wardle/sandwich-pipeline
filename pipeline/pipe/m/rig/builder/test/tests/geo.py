from maya import cmds
from maya.api.OpenMaya import MFnDependencyNode

from .. import RigBuildTest
from ..common import (
    GEO_GROUP_NAME,
    GEO_SET_NAME,
    format_max_items,
    get_all_visible_meshes,
    get_dag_path,
    is_control,
)


class TestGeoInSet(RigBuildTest):
    """
    Checks that the scene has no visible geometry that isn't in the geo set.
    This set is used for animation export and as such all non-control visible geometry should be in it.
    """

    def __init__(self):
        super().__init__("All geometry in set")

    def run(self) -> bool:
        visible_geo: set[str] = get_all_visible_meshes()
        problem_meshes: set[str]
        try:
            meshes_in_set: list[str] = cmds.sets(GEO_SET_NAME, query=True)  # type: ignore
            visible_meshes_not_in_set = visible_geo - set(meshes_in_set)
            problem_meshes = set(
                mesh for mesh in visible_meshes_not_in_set if not is_control(mesh)
            )
        except ValueError:
            problem_meshes = visible_geo

        if problem_meshes:
            self.log_warn(
                f"Scene has geometry that isn't in the geo set: "
                f'{format_max_items(problem_meshes, "mesh(es)")} need added to the "{GEO_SET_NAME}" set.'
            )
            return False
        else:
            self.log_success()
            return True


class TestGeoInGroup(RigBuildTest):
    """
    Checks that the scene has no visible geometry that isn't in the geo group.
    This group is used for animation export and as such all non-control visible geometry should be in it.
    """

    def __init__(self):
        super().__init__("All geometry in group")

    def run(self) -> bool:
        if not cmds.objExists(GEO_GROUP_NAME):
            self.log_warn(f'Scene missing the geo group "{GEO_GROUP_NAME}"')
            return False
        visible_geo: set[str] = get_all_visible_meshes()
        objects_in_group: list[str] = (
            cmds.listRelatives(GEO_GROUP_NAME, allDescendents=True) or []
        )
        visible_meshes_not_in_group = visible_geo - set(objects_in_group)
        problem_meshes = set(
            mesh for mesh in visible_meshes_not_in_group if not is_control(mesh)
        )
        if problem_meshes:
            self.log_warn(
                f"Scene has geometry that isn't in the geo group: "
                f'{format_max_items(problem_meshes, "mesh(es)")} need added to the "{GEO_GROUP_NAME}" group.'
            )
            return False
        else:
            self.log_success()
            return True


def _get_effective_display_type(object: str) -> int:
    """
    Returns the effective overrideDisplayType considering inheritance.
    0 = Normal, 1 = Template, 2 = Reference
    """
    path = get_dag_path(object)

    while True:
        fn = MFnDependencyNode(path.node())
        override_enabled = fn.findPlug("overrideEnabled", False).asBool()
        if override_enabled:
            return fn.findPlug("overrideDisplayType", False).asInt()
        if path.length() == 0:
            break
        try:
            path.pop()  # move to parent
        except RuntimeError:
            break
    return 0  # default: Normal


class TestGeoNotSelectable(RigBuildTest):
    """
    Checks that the scene has no visible geometry that is selectable (other than controls).
    """

    def __init__(self):
        super().__init__("No selectable geometry")

    def run(self) -> bool:
        visible_geo: set[str] = get_all_visible_meshes()
        selectable_geo = set(
            geo for geo in visible_geo if _get_effective_display_type(geo) == 0
        )
        selectable_visible_geo = visible_geo & selectable_geo
        problem_geo = set(geo for geo in selectable_visible_geo if not is_control(geo))
        if problem_geo:
            self.log_warn(
                f"Scene has geometry that is selectable: "
                f'{format_max_items(problem_geo, "mesh(es)")} need to set to reference display to make them unselectable.'
            )
            return False
        else:
            self.log_success()
            return True
