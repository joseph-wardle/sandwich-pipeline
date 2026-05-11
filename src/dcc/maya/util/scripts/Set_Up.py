import maya.cmds as cmds


def setup():
    # Define the main group name and child group names
    main_group = "WORLD"
    child_groups = [
        "CAM",
        "ENVIRONMENT",
        "CHARACTER_RIGS",
        "PROPS",
        "FX",
        "LIGHTING",
        "OTHER",
    ]

    # Create the main WORLD group if it doesn't exist
    if not cmds.objExists(main_group):
        cmds.group(empty=True, name=main_group)
    else:
        print(f"{main_group} already exists.")

    # Create child groups and parent them to WORLD
    for grp in child_groups:
        if not cmds.objExists(grp):
            new_grp = cmds.group(empty=True, name=grp)
            cmds.parent(new_grp, main_group)
        else:
            print(f"{grp} already exists.")
