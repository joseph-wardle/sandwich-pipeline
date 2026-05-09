import maya.cmds as cmds


def get_next_index(base_name):
    i = 1
    while True:
        name = f"{base_name}{str(i).zfill(2)}"
        if not cmds.objExists(name):
            return str(i).zfill(2)
        i += 1


def create_point_light_control():
    # Determine next available index
    index = get_next_index("point_light_")

    # Names
    ctrl_name = f"point_light_{index}_CTRL"
    offset_name = f"point_light_{index}_OFFSET"
    light_name = f"point_light_{index}"

    # Create NURBS circle controller
    ctrl = cmds.circle(name=ctrl_name, normal=[0, 1, 0], radius=1)[0]  # type: ignore
    cmds.scale(17, 17, 17, ctrl)  # type: ignore
    cmds.makeIdentity(ctrl, apply=True, scale=True, translate=True, rotate=True)  # type: ignore
    cmds.delete(ctrl, constructionHistory=True)  # type: ignore

    # Create OFFSET null group
    offset_grp = cmds.group(empty=True, name=offset_name)

    # Parent OFFSET under controller
    cmds.parent(offset_grp, ctrl)  # type: ignore

    # Create Point Light
    light_shape = cmds.pointLight(name=light_name)
    light_transform = cmds.listRelatives(light_shape, parent=True)[0]  # type: ignore
    light_transform = cmds.rename(light_transform, light_name)
    light_shape = cmds.listRelatives(light_transform, shapes=True)[0]

    # Set light attributes
    cmds.setAttr(f"{light_shape}.decayRate", 2)  # Quadratic decay  # type: ignore
    cmds.setAttr(f"{light_shape}.intensity", 100000)  # type: ignore
    cmds.setAttr(f"{light_shape}.useDepthMapShadows", 1)  # type: ignore
    cmds.setAttr(f"{light_shape}.dmapResolution", 16384)  # type: ignore

    # Parent light under OFFSET group
    cmds.parent(light_transform, offset_grp)

    # Add custom "Light_Intensity" attribute to controller
    attr_name = "Light_Intensity"
    if not cmds.attributeQuery(attr_name, node=ctrl, exists=True):  # type: ignore
        cmds.addAttr(ctrl, longName=attr_name, attributeType="double", keyable=True)  # type: ignore

    # Connect attribute to light intensity
    cmds.connectAttr(f"{ctrl}.{attr_name}", f"{light_shape}.intensity", force=True)
    cmds.setAttr(f"{ctrl}.{attr_name}", 100000)  # Set default value  # type: ignore

    print(f"✅ Created: {ctrl} → {offset_grp} → {light_name}")
    return ctrl, offset_grp, light_name


# Run it
create_point_light_control()
