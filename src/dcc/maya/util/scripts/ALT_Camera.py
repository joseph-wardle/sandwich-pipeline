import os

import maya.cmds as cmds
from shared.util import get_groups_path

# Corrected rig paths
RIG_PATH = get_groups_path() / "previs/Rigs/boboShotCam_v01.mb"


class ReferenceAndMatchRig:
    def launch(self):
        # UI
        if cmds.window("rigMatchUI", exists=True):
            cmds.deleteUI("rigMatchUI")

        rigs = self.get_rigs_with_cam_namespace()
        if not rigs:
            cmds.warning("No rigs with 'CAM' in namespace found.")
            return

        window = cmds.window(
            "rigMatchUI", title="Rig Reference and Match", widthHeight=(400, 150)
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=10, columnAlign="center")

        cmds.text(label="Select Target Rig (CAM):")
        self.rig_option_menu = cmds.optionMenu()
        for rig in rigs:
            cmds.menuItem(label=rig)

        cmds.button(label="Reference and Match Rig", command=self.on_apply)

        cmds.showWindow(window)  # type: ignore

    def get_rigs_with_cam_namespace(self):
        rigs = []
        for ref in cmds.file(query=True, reference=True) or []:  # type: ignore
            namespace = cmds.referenceQuery(ref, namespace=True).replace(":", "")  # type: ignore
            if "CAM" in namespace:
                rigs.append(namespace)
        return rigs

    def get_top_level_transforms(namespace):
        return [
            obj
            for obj in cmds.ls(f"{namespace}:*", transforms=True)
            if ":" not in obj.split("|")[-1]
        ]

    def generate_new_namespace(self, base_ns):
        base_name = f"{base_ns}_ALT"
        i = 1
        while cmds.namespace(exists=f"{base_name}{i:02}"):
            i += 1
        return f"{base_name}{i:02}"

    def match_transforms(self, source_ns, target_ns):
        control_names = [
            "world_CTRL",
            "main_CTRL",
            "dolly_CTRL",
            "tilt_pan_CTRL",
            "ClippingPlane_CTRL",
            "focusDistance_CTRL",
        ]

        for ctrl in control_names:
            source_ctrl = f"{source_ns}:{ctrl}"
            target_ctrl = f"{target_ns}:{ctrl}"

            if cmds.objExists(source_ctrl) and cmds.objExists(target_ctrl):
                pos = cmds.xform(target_ctrl, query=True, ws=True, t=True)  # type: ignore
                rot = cmds.xform(target_ctrl, query=True, ws=True, ro=True)  # type: ignore

                cmds.xform(source_ctrl, ws=True, t=pos)  # type: ignore
                cmds.xform(source_ctrl, ws=True, ro=rot)  # type: ignore
            else:
                print(f"Skipping '{ctrl}' — not found in one of the rigs.")

    def on_apply(self, *args):
        rig_path = RIG_PATH

        if not os.path.exists(rig_path):
            cmds.warning(f"Rig file not found: {rig_path}")
            return

        selected_rig_ns = cmds.optionMenu(self.rig_option_menu, query=True, value=True)  # type: ignore
        new_ns = self.generate_new_namespace(selected_rig_ns)

        # Reference rig
        cmds.file(rig_path, reference=True, namespace=new_ns)  # type: ignore

        self.match_transforms(f"{new_ns}", f"{selected_rig_ns}")
        cmds.confirmDialog(
            title="Success",
            message=f"Rig referenced and matched as {new_ns}",
            button=["OK"],
        )
