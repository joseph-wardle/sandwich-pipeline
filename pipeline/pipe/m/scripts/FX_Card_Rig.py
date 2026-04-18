import maya.cmds as cmds
import os

from shared.util import get_previs_path

# Cross-platform rig path (get_previs_path handles OS detection)
RIG_PATH = str(get_previs_path() / "Rigs/FX_card_rig_v02.ma")


def reference_rig_with_namespace(rig_path, base_namespace):
    # Generate a unique namespace
    i = 1
    namespace = base_namespace
    while cmds.namespace(exists=namespace):
        namespace = f"{base_namespace}_{i}"
        i += 1

    # Reference the file
    try:
        cmds.file(rig_path, reference=True, namespace=namespace)
        cmds.inViewMessage(
            amg=f"Referenced <hl>{rig_path}</hl> as <hl>{namespace}</hl>",
            pos="topCenter",
            fade=True,
        )
    except Exception as e:
        cmds.error(f"Failed to reference rig: {e}")


def open_reference_ui():
    # Close previous window if open
    if cmds.window("rigRefUI", exists=True):
        cmds.deleteUI("rigRefUI")

    window = cmds.window("rigRefUI", title="Rig Reference Tool", sizeable=False)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=10, columnAlign="center")

    cmds.text(label="Enter reference name:")
    ref_name_field = cmds.textField(placeholderText="e.g., myCharacter")

    def on_submit(*args):
        ref_name = cmds.textField(ref_name_field, q=True, text=True)

        if not ref_name:
            cmds.warning("Please enter a reference name.")
            return

        if not os.path.exists(RIG_PATH):
            cmds.error(f"Rig path not found: {RIG_PATH}")
            return

        reference_rig_with_namespace(RIG_PATH, ref_name)
        cmds.deleteUI("rigRefUI")

    cmds.button(label="Reference Rig", command=on_submit)

    cmds.showWindow(window)
