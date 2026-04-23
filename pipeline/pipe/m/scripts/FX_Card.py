import os

import maya.cmds as cmds


# --- Utility: Get next available plane name ---
def get_next_plane_name(base="FX_card_", padding=2):
    i = 1
    while True:
        name = f"{base}{str(i).zfill(padding)}"
        if not cmds.objExists(name):
            return name
        i += 1


# --- Ask user for material type ---
def get_material_type():
    result = cmds.confirmDialog(
        title="Choose Material",
        message="Which material would you like to use?",
        button=["Lambert", "Surface Shader", "Cancel"],
        defaultButton="Lambert",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    if result == "Cancel":
        return None
    return result


# --- Ask user for texture file ---
def get_texture_file():
    path = cmds.fileDialog2(fileMode=1, caption="Select a Texture File")
    if path:
        return path[0]
    return None


# --- Main logic ---
def FXCard():
    plane_name = get_next_plane_name()
    plane: str = cmds.polyPlane(  # type: ignore
        name=plane_name, width=10, height=10, subdivisionsX=3, subdivisionsY=3
    )[0]

    material_type = get_material_type()
    if not material_type:
        cmds.delete(plane)
        cmds.warning("User cancelled the operation.")
        raise RuntimeError("Cancelled")

    texture_path = get_texture_file()
    if not texture_path:
        cmds.delete(plane)
        cmds.warning("No texture selected.")
        raise RuntimeError("No texture selected")

    # === Create shader nodes ===
    shader_name = f"{plane_name}_mat"
    file_node = cmds.shadingNode(
        "file", asTexture=True, isColorManaged=True, name=f"{shader_name}_file"
    )
    place2d = cmds.shadingNode(
        "place2dTexture", asUtility=True, name=f"{shader_name}_place2d"
    )

    # Connect place2dTexture to file texture
    attrs = [
        "coverage",
        "translateFrame",
        "rotateFrame",
        "mirrorU",
        "mirrorV",
        "stagger",
        "wrapU",
        "wrapV",
        "repeatUV",
        "offset",
        "rotateUV",
        "noiseUV",
        "vertexUvOne",
        "vertexUvTwo",
        "vertexUvThree",
        "vertexCameraOne",
    ]
    for attr in attrs:
        cmds.connectAttr(f"{place2d}.{attr}", f"{file_node}.{attr}", force=True)
    cmds.connectAttr(f"{place2d}.outUV", f"{file_node}.uvCoord", force=True)
    cmds.connectAttr(
        f"{place2d}.outUvFilterSize", f"{file_node}.uvFilterSize", force=True
    )

    # Set the selected texture path
    cmds.setAttr(f"{file_node}.fileTextureName", texture_path, type="string")

    # === Create shader and connect texture ===
    if material_type == "Lambert":
        shader = cmds.shadingNode("lambert", asShader=True, name=shader_name)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color", force=True)
        cmds.connectAttr(
            f"{file_node}.outTransparency", f"{shader}.transparency", force=True
        )

    elif material_type == "Surface Shader":
        shader = cmds.shadingNode("surfaceShader", asShader=True, name=shader_name)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.outColor", force=True)
        cmds.connectAttr(
            f"{file_node}.outTransparency", f"{shader}.outTransparency", force=True
        )

    # === Assign shader to object ===
    shading_group = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True, name=f"{shader_name}SG"
    )
    cmds.connectAttr(f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)
    cmds.sets(plane, e=True, forceElement=shading_group)  # type: ignore

    print(
        f"✅ Created {plane} with a {material_type} material using {os.path.basename(texture_path)}"
    )
