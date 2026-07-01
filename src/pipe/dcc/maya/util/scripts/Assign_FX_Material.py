import maya.cmds as cmds


def assign_texture_with_dropdown():
    # Get all materials in the scene
    materials = cmds.ls(materials=True)

    if not materials:
        cmds.warning("No materials found in the scene.")
        return

    # Delete window if it already exists
    if cmds.window("shaderAssignWin", exists=True):
        cmds.deleteUI("shaderAssignWin")

    # Create window
    window = cmds.window(
        "shaderAssignWin", title="Assign Texture to Shader", sizeable=False
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=10, columnAlign="center")

    cmds.text(label="Select a shader:")
    #  shader_menu = cmds.optionMenu("shaderDropdown", width=300)
    for mat in materials:
        cmds.menuItem(label=mat)

    cmds.button(
        label="Select Texture and Apply", command=lambda *_: continue_with_texture()
    )
    cmds.button(
        label="Cancel", command=lambda *_: cmds.deleteUI("shaderAssignWin", window=True)
    )

    cmds.showWindow(window)  # type: ignore


def continue_with_texture():
    selected_shader = cmds.optionMenu("shaderDropdown", query=True, value=True)

    # Prompt user to select a file texture
    file_path = cmds.fileDialog2(
        fileFilter="Image Files (*.png *.jpg *.exr *.tga *.tiff)",
        dialogStyle=2,
        fileMode=1,
    )

    if not file_path:
        cmds.warning("No file selected. Operation cancelled.")
        return

    file_path = file_path[0]

    # Ask if user wants to assign transparency
    apply_transparency = cmds.confirmDialog(
        title="Assign Transparency?",
        message="Do you want to assign this texture to outTransparency as well?",
        button=["Yes", "No"],
        defaultButton="Yes",
        cancelButton="No",
        dismissString="No",
    )

    # Find an existing file node connected to the selected shader
    file_nodes = cmds.listConnections(
        f"{selected_shader}.outColor", source=True, destination=False, type="file"
    )

    if not file_nodes:
        cmds.warning(
            f"No file texture found for '{selected_shader}'. Cannot assign texture."
        )
        return

    file_node = file_nodes[0]

    # Set the file path for the texture
    cmds.setAttr(f"{file_node}.fileTextureName", file_path, type="string")

    # Connect to transparency if requested
    if apply_transparency == "Yes":
        cmds.connectAttr(
            f"{file_node}.outTransparency",
            f"{selected_shader}.outTransparency",
            force=True,
        )
        print(
            f"🎨 File '{file_path}' assigned to both outColor and outTransparency of '{selected_shader}'."
        )
    else:
        print(
            f"🎨 File '{file_path}' assigned only to outColor of '{selected_shader}'."
        )

    # Close the window
    if cmds.window("shaderAssignWin", exists=True):
        cmds.deleteUI("shaderAssignWin", window=True)
