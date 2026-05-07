from maya import cmds

from .. import RigBuildTest
from ..common import CONTROLS_SET_NAME, format_max_items, get_all_controls_by_name


class TestControlsZeroed(RigBuildTest):
    """
    Checks that the scene has no controls that aren't zeroed (translate rotate scale shear).
    If a control has a dagPose node that can be used to return to rest pose, the control will also pass.
    """

    def __init__(self):
        super().__init__("All controls zeroed")

    def run(self) -> bool:
        controls = get_all_controls_by_name()

        EPSILON = 0.0001
        defaults: dict[str, tuple[float, float, float]] = {
            "translate": (0.0, 0.0, 0.0),
            "rotate": (0.0, 0.0, 0.0),
            "shear": (0.0, 0.0, 0.0),
            "scale": (1.0, 1.0, 1.0),
        }
        problem_controls: list[str] = []

        def has_dag_pose(control: str) -> bool:
            pose_nodes = cmds.listConnections(
                f"{control}.message", source=False, destination=True, type="dagPose"
            )
            return True if pose_nodes else False

        for control in controls:
            # If the control has a dagPose that can be used to return the control to rest positon,
            # that's better than zeroing anyway. We won't enforce zeroing for this control.
            if has_dag_pose(control):
                continue

            for attr, default_val in defaults.items():
                full_attr_path = f"{control}.{attr}"
                # If the attribute can't be set (locked) then it being zeroed doesn't matter to the animator.
                if not cmds.getAttr(full_attr_path, settable=True):
                    continue
                attr_val = cmds.getAttr(full_attr_path)[0]
                if any(
                    abs(current - default) > EPSILON
                    for current, default in zip(attr_val, default_val)
                ):
                    problem_controls.append(control)
                    break  # Move to next control as soon as one error is found

        if problem_controls:
            self.log_warn(
                "Scene has controls with non zeroed transforms: "
                f"{format_max_items(problem_controls, 'controls')}"
            )
            return False
        else:
            self.log_success()
            return True


class TestControlsTagged(RigBuildTest):
    """
    Checks that the scene has no controls that aren't connected to a Maya `controller` node.
    This is for performance and easy categorization of what is a control.
    """

    def __init__(self):
        super().__init__("All controls tagged")

    def run(self) -> bool:
        controls = get_all_controls_by_name()
        tagged_controls: list[str] = cmds.controller(query=True, allControllers=True)  # type: ignore

        problem_controls = set(controls) - set(tagged_controls)
        if problem_controls:
            self.log_warn(
                "Scene has controls that aren't tagged as controllers: "
                f"{format_max_items(problem_controls, 'controls')}"
            )
            return False
        else:
            self.log_success()
            return True


class TestControlsInSet(RigBuildTest):
    """
    Checks that the scene has no controls that aren't in the controls set.
    This is for easy selection of all controls by the animator.
    """

    def __init__(self):
        super().__init__("All controls in set")

    def run(self) -> bool:
        controls = get_all_controls_by_name()
        problem_controls: set[str]
        try:
            controls_in_set: list[str] = cmds.sets(CONTROLS_SET_NAME, query=True)  # type: ignore
            problem_controls = set(controls) - set(controls_in_set)
        except ValueError:
            problem_controls = set(controls)
        if problem_controls:
            self.log_warn(
                "Scene has controls that aren't in the controls set: "
                f'{format_max_items(problem_controls, "control(s)")} need added to the "{CONTROLS_SET_NAME}" set.'
            )
            return False
        else:
            self.log_success()
            return True
