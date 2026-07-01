import maya.cmds as cmds


def get_next_unique_index(base_name="spotlight", start=1):
    index = start
    while True:
        # Check if the main spotlight transform exists with this index
        name_to_check = f"{base_name}_{str(index).zfill(2)}"
        if not cmds.objExists(name_to_check):
            return index
        index += 1


def create_spotlight_rig_auto_index():
    base_name = "spotlight"
    index = get_next_unique_index(base_name)
    index_str = str(index).zfill(2)

    # Names with number at the end
    light_transform_name = f"{base_name}_{index_str}"
    light_shape_name = f"{base_name}Shape_{index_str}"
    light_offset_name = f"{base_name}_OFFSET_{index_str}"
    root_ctrl_name = f"{base_name}_root_CTRL_{index_str}"
    root_offset_name = f"{base_name}_root_OFFSET_{index_str}"
    tumble_ctrl_name = f"{base_name}_tumble_CTRL_{index_str}"
    tumble_offset_name = f"{base_name}_tumble_OFFSET_{index_str}"

    # --- Create spotlight transform and shape ---
    light_transform = cmds.createNode("transform", name=light_transform_name)
    light_shape = cmds.createNode(
        "spotLight", name=light_shape_name, parent=light_transform
    )

    cmds.setAttr(f"{light_shape}.decayRate", 2)  # Quadratic decay  # type: ignore
    cmds.setAttr(f"{light_shape}.intensity", 100000)  # type: ignore
    cmds.setAttr(f"{light_shape}.useDepthMapShadows", 1)  # type: ignore
    cmds.setAttr(f"{light_shape}.dmapResolution", 16384)  # type: ignore
    cmds.setAttr(f"{light_transform}.scale", 72, 72, 72, type="double3")  # type: ignore

    # --- Create root control ---
    root_ctrl: str = cmds.circle(name=root_ctrl_name, normal=(0, 1, 0))[0]  # type: ignore
    cmds.setAttr(f"{root_ctrl}.scale", 67, 67, 67, type="double3")  # type: ignore
    cmds.setAttr(f"{root_ctrl}.translateY", -8)  # type: ignore
    cmds.makeIdentity(root_ctrl, apply=True, t=1, r=1, s=1)  # type: ignore

    # --- Create tumble control ---
    tumble_ctrl: str = cmds.circle(name=tumble_ctrl_name, normal=(0, 1, 0))[0]  # type: ignore
    cmds.setAttr(f"{tumble_ctrl}.scale", 34, 34, 34, type="double3")  # type: ignore
    cmds.setAttr(f"{tumble_ctrl}.rotateX", 90)  # type: ignore
    cmds.makeIdentity(tumble_ctrl, apply=True, t=1, r=1, s=1)  # type: ignore

    # --- Create offset groups ---
    light_offset = cmds.group(light_transform, name=light_offset_name)
    tumble_offset = cmds.group(tumble_ctrl, name=tumble_offset_name)
    root_offset = cmds.group(root_ctrl, name=root_offset_name)

    # --- Build hierarchy using variables only ---
    cmds.parent(light_offset, tumble_ctrl)  # spotlight_OFFSET_## under tumble_CTRL
    cmds.parent(tumble_offset, root_ctrl)  # tumble_OFFSET_## under root_CTRL
    cmds.parent(tumble_ctrl, tumble_offset)  # tumble_CTRL under tumble_OFFSET
    cmds.parent(root_ctrl, root_offset)  # root_CTRL under root_OFFSET

    print(f"✅ Created spotlight rig '{light_transform_name}' successfully.")


# Example usage — just run this to create one rig with unique naming:
create_spotlight_rig_auto_index()
