import datetime
import json
import os
import subprocess
from typing import Any

import nuke
from env_sg import DB_Config
from pipe.core.playblast.naming import edit_shot_directory, next_delivery_name
from pipe.core.util.paths import get_production_path

from pipe.core.shotgrid import Playlist, ShotGrid, ShotGridError, ShotGridNotFound

TAILS = 5
EXPORTED_KNOB = "exported_mov"

DEPT_DIR_MAP: dict[str, str] = {
    "Lighting": "lighting",
    "Compositing": "comp",
    "FX": "fx",
    "Shading": "lighting",
}


def _set_knob_value(node: Any, knob_name: str, value: Any) -> None:
    node.knob(knob_name).setValue(value)


def make_text_nodes():
    # Text Padding
    rl_padding = 25
    tb_padding = 25
    font_size = 25
    frame_height = 816
    frame_width = 1920

    # Frame Number
    frame_num_text = nuke.createNode("Text2", "font_size 30")
    _set_knob_value(frame_num_text, "font_size", font_size)
    _set_knob_value(
        frame_num_text,
        "box",
        [rl_padding, tb_padding, frame_width - rl_padding, frame_height - tb_padding],
    )
    _set_knob_value(frame_num_text, "xjustify", "right")
    _set_knob_value(frame_num_text, "yjustify", "bottom")
    _set_knob_value(frame_num_text, "enable_background", 1)
    frame_num_text.setName("Frame_Number")
    # message set below to force the font size to update

    # Department
    department_text = nuke.createNode("Text2")
    _set_knob_value(department_text, "font_size", font_size)
    _set_knob_value(
        department_text,
        "box",
        [
            rl_padding,
            tb_padding * 2 + font_size * 3,
            frame_width - rl_padding,
            frame_height,
        ],
    )
    _set_knob_value(department_text, "xjustify", "left")
    _set_knob_value(department_text, "yjustify", "bottom")
    _set_knob_value(department_text, "enable_background", 1)
    department_text.setName("department_text")
    dropdown_knob = nuke.Enumeration_Knob(
        "departmentDropdown",
        "departmentDropdown",
        ["Lighting", "Compositing", "FX", "Shading"],
    )
    department_text.addKnob(dropdown_knob)
    # message set below to force the font size to update

    # Shot Code
    shot_code_text = nuke.createNode("Text2")
    _set_knob_value(shot_code_text, "font_size", font_size)
    _set_knob_value(
        shot_code_text,
        "box",
        [
            rl_padding,
            tb_padding + font_size * 2,
            frame_width - rl_padding,
            frame_height,
        ],
    )
    _set_knob_value(shot_code_text, "xjustify", "right")
    _set_knob_value(shot_code_text, "yjustify", "bottom")
    _set_knob_value(shot_code_text, "enable_background", 1)
    shot_code_text.setName("Shot_Code")
    # message set below to force the font size to update

    # date
    date_text = nuke.createNode("Text2")
    _set_knob_value(date_text, "font_size", font_size)
    _set_knob_value(
        date_text,
        "box",
        [
            rl_padding,
            tb_padding + font_size * 2,
            frame_width - rl_padding,
            frame_height,
        ],
    )
    _set_knob_value(date_text, "xjustify", "left")
    _set_knob_value(date_text, "yjustify", "bottom")
    _set_knob_value(date_text, "enable_background", 1)
    date_text.setName("date")
    # message set below to force the font size to update

    # user name
    name_text = nuke.createNode("Text2")
    _set_knob_value(name_text, "font_size", font_size)
    _set_knob_value(
        name_text,
        "box",
        [rl_padding, tb_padding, frame_width - rl_padding, frame_height - tb_padding],
    )
    _set_knob_value(name_text, "xjustify", "left")
    _set_knob_value(name_text, "yjustify", "bottom")
    _set_knob_value(name_text, "enable_background", 1)
    name_text.setName("name")
    # message set below to force the font size to update

    blur_node = nuke.createNode("Blur")
    nuke.delete(
        blur_node
    )  # What's this for? Idk why, but this makes it so the user name isn't gigantic. idk why.

    # set text node message values (because if I don't do it here, the font size won't update in time and you'll just have big massive font sizes)
    return [frame_num_text, shot_code_text, date_text, name_text, department_text]


def update_text_messages(
    frame_num_text, shot_code_text, date_text, name_text, department_text
):
    _set_knob_value(frame_num_text, "message", "Frame: [frame]")
    _set_knob_value(shot_code_text, "message", get_shot_code())
    _set_knob_value(date_text, "message", get_date())
    _set_knob_value(name_text, "message", str(get_users_name()))

    # department dropdown and text
    _set_knob_value(department_text, "message", "[value departmentDropdown]")


def get_in_out():
    """
    Returns the in/out cut frames for the current shot.
    Falls back to the script's own frame range if the shot cannot be found in
    ShotGrid or is missing cut_in / cut_out there.
    """
    curr_shot = get_shot_code()
    conn = ShotGrid.connect(DB_Config)
    try:
        shot = conn.get_shot(code=curr_shot)
        return list(shot.frame_range)
    except ShotGridError as exc:
        nuke.message(
            f"Could not get frame range for '{curr_shot}' from ShotGrid: {exc}\n"
            "Using script's frame range instead."
        )
        first = int(nuke.root()["first_frame"].value())
        last = int(nuke.root()["last_frame"].value())
        return [first, last]


def get_date():
    today = datetime.date.today()
    formatted_date = today.strftime("%m/%d/%Y")
    return formatted_date


def increment_version_num(curr_version):
    num_str = curr_version.split("_")[1]
    num_int = int(num_str) + 1
    return f"V_{num_int:0{len(num_str)}d}"


def get_version_num():
    # path to the shot versions json
    json_path = get_production_path() / "json/shot_versions.json"

    with open(json_path, "r") as f:
        shot_data = json.load(f)

    # I think this should return the version number, with V_001 is the default. Hopefully.
    shot_code = get_shot_code()
    if shot_data.get(shot_code):
        return increment_version_num(
            shot_data.get(shot_code)
        )  # if a shot code already exists, you gotta increment it.
    else:
        return "V_001"  # If the shot has never been rendered out before


def get_shot_code():
    """A comp is `shot/<shot>/comp/<any name>.nk`, so the shot is the folder,
    not the file name."""
    return os.path.basename(os.path.dirname(os.path.dirname(nuke.root().name())))


def get_users_name():
    """
    Returns the full name corresponding to the current user's login as defined in usernames.json.
    If the username is not found in the JSON file, returns None.
    """
    # Get the current login username
    username = os.getlogin()
    json_path = get_production_path() / "json/usernames.json"

    # Open and load the JSON file.
    with open(json_path, "r") as f:
        user_data = json.load(f)

    # Return the corresponding name for the username.
    # If the key is not found, .get() will return None.
    return user_data.get(username)


def get_output_file_info_mov(group):
    """The next edit delivery for this shot. The MOV_write creates the folder."""
    currDept = getDepartment(group)
    department = DEPT_DIR_MAP.get(currDept, currDept.lower())
    shot_code = get_shot_code()
    folder = edit_shot_directory(shot_code)
    new_file_name = next_delivery_name(folder, shot_code, department) + ".mov"
    return [new_file_name, str(folder)]


def apply_mov_path_to_ui_and_write(group):
    """Point the path label and MOV_write at the next delivery."""
    new_file_name, folder_path = get_output_file_info_mov(group)
    full_path = os.path.join(folder_path, new_file_name)
    group["mov_export_path"].setValue(full_path)
    group.node("MOV_write")["file"].setValue(full_path)
    return full_path


def _button(function_name):
    """A saved comp keeps a knob's script as text, so the script only names a
    function here; a fix to the function then reaches nodes made before it."""
    return f"import skd_write_node; skd_write_node.{function_name}(nuke.thisNode())"


def export_mov(group):
    # A second export is a new delivery; it must not overwrite the first.
    path = apply_mov_path_to_ui_and_write(group)
    first_frame = int(group["export_frame_in"].value())
    last_frame = int(group["export_frame_out"].value())
    nuke.execute(group.node("MOV_write"), first_frame, last_frame, 1)
    group[EXPORTED_KNOB].setValue(path)


def open_folder(group):
    folder = os.path.dirname(group["mov_export_path"].value())
    if not os.path.isdir(folder):
        nuke.message("This folder does not exist yet, but it will after you export.")
        return
    subprocess.Popen(["xdg-open", folder])


def add_tails(group):
    """Sets the range from the shot's, so pressing it twice adds nothing more."""
    first, last = get_in_out()
    group["export_frame_in"].setValue(first - TAILS)
    group["export_frame_out"].setValue(last + TAILS)
    nuke.message(
        f"Frame range is now {first - TAILS}-{last + TAILS}: "
        f"the shot plus {TAILS} tail frames on each side."
    )


def knob_changed(group, knob):
    if knob.name() == "departmentDropdown":
        apply_mov_path_to_ui_and_write(group)


def make_timecode_node():
    """Editorial lines a new delivery up by timecode, so the movie's timecode
    must count shot frames: frame 1001 is 00:00:41:17."""
    timecode_node = nuke.createNode("AddTimeCode")
    _set_knob_value(timecode_node, "startcode", "00:00:00:00")
    _set_knob_value(timecode_node, "fps", 24)
    _set_knob_value(timecode_node, "useFrame", True)
    _set_knob_value(timecode_node, "frame", 0)
    return timecode_node


def make_MOV_node():
    # The file path is set by makeUI, once the department dropdown exists.
    write_node = nuke.createNode("Write")
    write_node.setName("MOV_write")
    write_node["file_type"].setValue("mov64")

    # Create directories automatically.
    write_node["create_directories"].setValue(1)

    # Other write node settings.
    write_node["colorspace"].setValue("Raw")
    write_node["transformType"].setValue(1)  # Display transform

    fmt = write_node.format()
    aspect = fmt.width() / float(fmt.height())

    if abs(aspect - (16.0 / 9.0)) < 0.001:
        # 16:9 → use DNxHD
        write_node["mov64_codec"].setValue(11)  # Avid DNxHD
        write_node["mov64_dnxhd_codec_profile"].setValue(1)  # DNxHD 422 10-bit 220 Mbps
    else:
        # anything else → stick with DNxHR
        write_node["mov64_codec"].setValue(12)  # Avid DNxHR
        # (you can also set mov64_dnxhr_profile if you need a specific DNxHR flavor)

    # update the version number json.
    shot_code = get_shot_code()
    version_num = get_version_num()
    json_path = str(get_production_path()) + "/json/shot_versions.json"
    command = (
        "import json, os\n"
        'json_path = "{json_path}"\n'
        'with open(json_path, "r") as f:\n'
        "    data = json.load(f)\n"
        'data["{shot_code}"] = "{version_num}"\n'
        'with open(json_path, "w") as f:\n'
        "    json.dump(data, f, indent=4)\n"
    ).format(json_path=json_path, shot_code=shot_code, version_num=version_num)
    write_node["afterRender"].setValue(command)

    return write_node


def update_mov_node(write_node):
    # get aspect ratio
    fmt = write_node.format()
    aspect = fmt.width() / float(fmt.height())

    # look up the actual dropdown indices at runtime
    # nuke.message("Codecs:\n" + "\n".join(f"{i}: {v}" for i,v in enumerate(vals))) #(if you ever need to see a list of codecs)
    # pick the right codec
    if abs(aspect - (16.0 / 9.0)) < 0.001:
        # 16:9 → DNxHD 422 10-bit 220Mbps
        write_node["mov64_codec"].setValue(12)
        write_node["mov64_dnxhd_codec_profile"].setValue(1)
    else:
        # anything else → DNxHR (HQ for example)
        write_node["mov64_codec"].setValue(13)
        write_node["mov64_dnxhd_codec_profile"].setValue(0)
        # if you want a specific DNxHR flavor you can also do:
        # write_node["mov64_dnxhr_profile"].setValue(<your-profile-index>)


def check_saved():
    current_script_name = get_shot_code()
    if current_script_name == "Root":
        nuke.message(
            "This nuke script isn't saved, so I don't know what shot you're wanting to write out! Please save your shot!"
        )
        return False
    else:
        return True


def makeUI(groupNode):
    mov_tab_name = "MOV Export"
    tab_knob = nuke.Tab_Knob(mov_tab_name)
    groupNode.addKnob(tab_knob)

    mov_export_button = nuke.PyScript_Knob(
        "mov_export", "Export MOV", _button("export_mov")
    )
    mov_export_path = nuke.Text_Knob("mov_export_path", "")
    exported_mov = nuke.String_Knob(EXPORTED_KNOB, "")
    exported_mov.setFlag(nuke.INVISIBLE)
    open_folder_button = nuke.PyScript_Knob(
        "open_folder", "Open Folder", _button("open_folder")
    )
    open_folder_button.clearFlag(nuke.STARTLINE)

    # frame range label
    frame_range = nuke.Text_Knob("frame_range", "")
    frame_range.setValue("Frame range is currently set to:")

    # frame ranges
    frame_in = nuke.Int_Knob("export_frame_in", "")
    frame_in.setValue(get_in_out()[0])
    frame_out = nuke.Int_Knob("export_frame_out", "")
    frame_out.setValue(get_in_out()[1])
    frame_out.clearFlag(nuke.STARTLINE)

    add_handles_button = nuke.PyScript_Knob(
        "add_shot_handles", "Add Tails", _button("add_tails")
    )
    add_handles_button.clearFlag(nuke.STARTLINE)

    # checkboxes
    checkbox1 = nuke.Boolean_Knob("disable_text", "Disable On Screen Text")

    # dividers
    divider1 = nuke.Text_Knob("divider1", "")
    divider2 = nuke.Text_Knob("divider2", "")

    # dropdown
    department_dropdown = nuke.Enumeration_Knob(
        "departmentDropdown", "", ["Lighting", "Compositing", "FX", "Shading"]
    )

    # Add all knobs
    groupNode.addKnob(mov_export_button)
    groupNode.addKnob(frame_range)
    groupNode.addKnob(frame_in)
    groupNode.addKnob(frame_out)
    groupNode.addKnob(add_handles_button)
    groupNode.addKnob(divider1)
    groupNode.addKnob(department_dropdown)
    groupNode.addKnob(checkbox1)
    groupNode.addKnob(divider2)
    groupNode.addKnob(mov_export_path)
    groupNode.addKnob(open_folder_button)
    groupNode.addKnob(exported_mov)

    # Add Send to Shotgrid UI
    groupNode.addKnob(nuke.Text_Knob("shotgrid_divider", ""))

    # Populate ShotGrid Task dropdown dynamically
    try:
        _conn = ShotGrid.connect(DB_Config)
        tasks = _conn.find_tasks(shot=getShot(), user=getShotGridUser())
        task_labels = [t.content for t in tasks if t.content]
    except Exception:
        task_labels = []
    task_knob = nuke.Enumeration_Knob("shotgrid_task", "ShotGrid Task", task_labels)
    groupNode.addKnob(task_knob)
    dept_knob = nuke.Enumeration_Knob(
        "sgDepartmentDropdown",
        "Department",
        ["Lighting", "Compositing", "FX", "Environment", "Shading"],
    )
    dept_knob.clearFlag(nuke.STARTLINE)
    groupNode.addKnob(dept_knob)

    # Description field
    desc_knob = nuke.Multiline_Eval_String_Knob(
        "shotgrid_description", "ShotGrid Description"
    )
    desc_knob.setValue("")
    groupNode.addKnob(desc_knob)

    # Send to ShotGrid button
    send_sg_btn = nuke.PyScript_Knob(
        "send_to_sg", "Send to ShotGrid", _button("create_new_shot_version")
    )
    groupNode.addKnob(send_sg_btn)
    apply_mov_path_to_ui_and_write(groupNode)

    groupNode.knob("knobChanged").setValue(
        "import skd_write_node; "
        "skd_write_node.knob_changed(nuke.thisNode(), nuke.thisKnob())"
    )


def createLinks(text_nodes, switch):
    switch["which"].setExpression("parent.disable_text")
    text_nodes[4]["departmentDropdown"].setExpression(
        "parent.departmentDropdown"
    )  # department


def getDepartment(group) -> str:
    return group["departmentDropdown"].value()


### This is where we start sending things back to Shotgrid ###
def getShot():
    _conn = ShotGrid.connect(DB_Config)
    try:
        return _conn.get_shot(code=get_shot_code())
    except ShotGridNotFound:
        nuke.message("Invalid shot code")
        return None


def getShotGridUser():
    _conn = ShotGrid.connect(DB_Config)
    username = get_users_name()
    if not username:
        nuke.message(
            "Username did not match any users in Shotgrid. Talk to your lead or the pipeline person."
        )
        raise Exception(
            "Username did not match any users in Shotgrid. Talk to your lead or the pipeline person."
        )
    try:
        return _conn.get_user(name=username)
    except ShotGridNotFound:
        nuke.message(
            f"No ShotGrid user found for '{username}'. Talk to your lead or the pipeline person."
        )
        raise


def getUserTask(group):
    """
    Returns the Task object corresponding to the user's selection in the ShotGrid Task dropdown.
    """
    selected_name = group["shotgrid_task"].value()
    shot = getShot()
    user = getShotGridUser()
    tasks = ShotGrid.connect(DB_Config).find_tasks(shot=shot, user=user)
    for t in tasks:
        if t.content == selected_name:
            return t
    return None


def getMostRecentPlaylist(group) -> Playlist | None:
    """
    Look at the departmentDropdown on this group,
    find all SG playlists whose code contains that department,
    parse the M/D/YY date prefix, and return the most recent one.
    """
    dept = group["sgDepartmentDropdown"].value()
    if not dept:
        nuke.message("Please pick a department first.")
        return None

    try:
        playlists = ShotGrid.connect(DB_Config).find_playlists(code_contains=dept)
    except ShotGridError as e:
        nuke.message(f"ShotGrid lookup failed: {e}")
        return None

    most_recent: Playlist | None = None
    latest_date = None
    for playlist in playlists:
        date_str = (playlist.code or "").split(" ", 1)[0]
        try:
            pl_date = datetime.datetime.strptime(date_str, "%m/%d/%y").date()
        except ValueError:
            continue
        if latest_date is None or pl_date > latest_date:
            latest_date = pl_date
            most_recent = playlist

    if most_recent is None:
        nuke.message(f"No playlists found for “{dept}”.")
        return None

    return most_recent


def create_new_shot_version(group):
    video_path = group[EXPORTED_KNOB].value()
    if not os.path.isfile(video_path):
        nuke.message("Export the MOV before sending it to ShotGrid.")
        return

    shot = getShot()
    user = getShotGridUser()
    playlist = getMostRecentPlaylist(group)

    if playlist:
        nuke.message(
            f"Using playlist: {playlist.code}\n"
            "\n"
            "Contact your lead if you need a newer playlist!"
        )
    else:
        if not nuke.ask(
            "Are you sure you want to continue? You will be creating a version that isn't attached to a dailies review."
        ):
            nuke.message(
                "Please select a valid playlist to continue. If you continue to recieve this error please talk to your lead"
            )
            return

    task = getUserTask(group)
    if not task:
        nuke.message("Please select a valid ShotGrid task.")
        return

    task_name = task.content
    version_name = f"{get_users_name()}_{task_name}_{get_version_num()}"
    description = (
        group["shotgrid_description"].value()
        if group.knob("shotgrid_description")
        else ""
    )

    # Create the Version row first, then upload the movie as a separate step so
    # an upload failure can be reported distinctly from a create failure.
    _conn = ShotGrid.connect(DB_Config)
    try:
        new_version = _conn.create_version(
            entity=shot,
            code=version_name,
            user=user,
            task=task,
            description=description,
            path_to_frames=video_path,
        )
    except Exception as e:
        nuke.message(f"ShotGrid version creation failed: {e}")
        return

    try:
        _conn.upload_movie(new_version, video_path)
    except Exception as e:
        nuke.message(f"Version '{version_name}' created, but movie upload failed: {e}")
        return

    if playlist is not None:
        try:
            _conn.link_to_playlist(new_version, playlist_id=playlist.id)
        except Exception as e:
            nuke.message(
                f"Version '{version_name}' uploaded, but adding it to playlist "
                f"'{playlist.code}' failed: {e}"
            )
            return

    nuke.message(
        f"ShotGrid version '{version_name}' created and movie uploaded successfully."
    )


### End return to shotgrid helper functions ###
def main():
    if check_saved():
        current_node = None
        selected_nodes = nuke.selectedNodes()
        if selected_nodes:
            current_node = selected_nodes[0]

        base_name = "BOBO_Write"
        final_name = base_name

        # Check if a node with the base name exists.
        if nuke.toNode(base_name) is not None:
            count = 2  # Start numbering at 2.
            final_name = "{}{}".format(base_name, count)
            # Increment count until a unique name is found.
            while nuke.toNode(final_name) is not None:
                count += 1
                final_name = "{}{}".format(base_name, count)

        # Create the group node and set its name to the unique name.
        groupNode = nuke.createNode("Group")
        groupNode["name"].setValue(final_name)

        # Enter the group to build its internal node graph.input
        groupNode.begin()  # ty:ignore[unresolved-attribute]

        # input_node
        input_node = nuke.createNode("Input")

        # reformat node
        reformat_node = nuke.createNode("Reformat")
        reformat_node["format"].setValue("Bobo_aspect_ratio")
        reformat_node.setInput(0, input_node)

        # All text nodes
        text_nodes = make_text_nodes()

        # Switch Node
        text_node_pos_x = text_nodes[3].xpos()
        text_node_pos_y = text_nodes[3].ypos()
        switcheroo = nuke.createNode("Switch")
        switcheroo.setInput(0, text_nodes[3])
        switcheroo.setInput(1, reformat_node)
        switcheroo.setXYpos(text_node_pos_x + 100, text_node_pos_y)

        # reformat node
        nuke.createNode("Reformat")

        make_timecode_node()

        # MOV node
        mov_node = make_MOV_node()

        # update text nodes messages
        update_text_messages(
            text_nodes[0], text_nodes[1], text_nodes[2], text_nodes[3], text_nodes[4]
        )

        # update settings in mov node
        update_mov_node(mov_node)

        # output Node
        output_node = nuke.createNode("Output")
        output_node.setInput(0, switcheroo)
        output_node.setXYpos(text_node_pos_x, text_node_pos_y + 100)

        makeUI(groupNode)
        createLinks(text_nodes, switcheroo)

        for n in nuke.allNodes():
            n.hideControlPanel()
        groupNode.end()  # ty:ignore[unresolved-attribute]

        groupNode.setSelected(True)

        if current_node:
            groupNode.setInput(0, current_node)

        groupNode["tile_color"].setValue(0xFF6699FF)  # Example: a blueish color


# main()
