import re

import maya.cmds as cmds
from core.util.util import get_previs_path

# Global variable to store the camera file path (cross-platform)
cameraFilePath = str(get_previs_path() / "Rigs/boboShotCam_v01.mb")

# Dictionary to track the last used shot number for each sequence
sequence_shot_tracker = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0, "G": 0}


# Function to get the last shot number from existing references for a specific sequence
def get_last_shot_num_for_sequence(sequence):
    refs: list[str] = cmds.file(q=True, reference=True)  # type: ignore
    shot_nums = []

    # Regex to match shot code format: "SequenceName_###"
    pattern = re.compile(rf"^{sequence}_(\d{{3}})_CAM$")

    for ref in refs:
        ns: str = cmds.file(ref, q=True, namespace=True)  # type: ignore
        match = pattern.search(ns)
        if match:
            # Extract the shot number and convert to integer
            shot_nums.append(int(match.group(1)))

    if shot_nums:
        return max(shot_nums)
    else:
        return sequence_shot_tracker[
            sequence
        ]  # Default from tracker if no existing references


def reference_camera():
    global cameraFilePath, sequence_shot_tracker

    # Check if the camera file path is set
    if cameraFilePath is None:
        cmds.error("Camera file path not provided!")
        return

    # Get the selected sequence name
    seqName: str = cmds.optionMenu("seqMenu", query=True, value=True)  # type: ignore

    # Get the last used shot number for the current sequence
    last_shot_num = get_last_shot_num_for_sequence(seqName)

    # If no references were found, start from 010
    if last_shot_num == 0:
        shotNum = 10
    else:
        shotNum = last_shot_num + 10  # Increment by 10

    # Update the tracker with the new shot number for this sequence
    sequence_shot_tracker[seqName] = shotNum

    # Create the shot code
    shotCode = seqName + "_" + "{:03d}".format(shotNum) + "_CAM"

    # Reference the camera file with the selected file path
    cmds.file(cameraFilePath, r=True, namespace=shotCode)  # type: ignore


# Reference the camera with the configured path
def reference_camera_from_ui():
    reference_camera()


# Create the main UI for selecting the operating system and sequence name
def show_camera_reference_ui():
    if cmds.window("CameraReferenceUI", exists=True):
        cmds.deleteUI("CameraReferenceUI")

    camera_window = cmds.window(
        "CameraReferenceUI",
        title="Reference Camera",
        widthHeight=(300, 150),
    )
    cmds.columnLayout(adjustableColumn=True)

    # Sequence selection
    cmds.text(label="Select Sequence Name:")
    cmds.optionMenu("seqMenu", label="Sequence Name")
    cmds.menuItem(label="A")
    cmds.menuItem(label="B")
    cmds.menuItem(label="C")
    cmds.menuItem(label="D")
    cmds.menuItem(label="E")
    cmds.menuItem(label="F")
    cmds.menuItem(label="G")

    # Button to reference the camera
    cmds.button(
        label="Reference Camera",
        command=lambda x: reference_camera_from_ui(),
    )

    cmds.showWindow(camera_window)  # type: ignore


# Call the UI to show the window
show_camera_reference_ui()
