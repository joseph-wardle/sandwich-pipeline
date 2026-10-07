import datetime
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import nuke
from env_sg import DB_Config
from pipe.core.playblast import ShotEntity
from pipe.core.playblast.naming import edit_shot_directory, next_delivery_name
from pipe.core.playblast.review import (
    PlayblastVersionUploadRequest,
    upload_playblast_version,
)
from pipe.core.shotgrid import Playlist, ShotGrid, ShotGridError, Task
from pipe.core.util.users import resolve_artist_display_name

TAILS = 5
# The last movie this node wrote, which is what Send to ShotGrid uploads.
EXPORTED_KNOB = "exported_mov"
# Dailies and edit movies stay 1080p whatever the comp's format.
REVIEW_HEIGHT = 1080
# The HUD was laid out on a 1080 frame; other formats scale it.
HUD_LAYOUT_HEIGHT = 1080
HUD_FONT_SIZE = 25

DEPARTMENTS: dict[str, str] = {
    "Lighting": "lighting",
    "Compositing": "comp",
    "FX": "fx",
    "CFX": "cfx",
}


def _set_knob_value(node: Any, knob_name: str, value: Any) -> None:
    node.knob(knob_name).setValue(value)


def _make_hud_text(
    name: str, font_size: float, bottom: float, top_inset: float, xjustify: str
) -> Any:
    text = nuke.createNode("Text2")
    _set_knob_value(text, "font_size", font_size)
    width = nuke.root().format().width()
    height = nuke.root().format().height()
    pad = font_size  # The padding has always matched the font size.
    _set_knob_value(text, "box", [pad, bottom, width - pad, height - top_inset])
    _set_knob_value(text, "xjustify", xjustify)
    _set_knob_value(text, "yjustify", "bottom")
    _set_knob_value(text, "enable_background", 1)
    text.setName(name)
    return text


def make_text_nodes():
    # Sized once, from the comp's format. Text2 fixes each character's size
    # when its message is set, so an expression on font_size would not follow
    # a later format change: rebuild the node after one.
    size = HUD_FONT_SIZE * nuke.root().format().height() / HUD_LAYOUT_HEIGHT
    frame_num_text = _make_hud_text("Frame_Number", size, size, size, "right")
    department_text = _make_hud_text("department_text", size, size * 5, 0, "left")
    department_text.addKnob(
        nuke.Enumeration_Knob(
            "departmentDropdown", "departmentDropdown", list(DEPARTMENTS)
        )
    )
    shot_code_text = _make_hud_text("Shot_Code", size, size * 3, 0, "right")
    date_text = _make_hud_text("date", size, size * 3, 0, "left")
    name_text = _make_hud_text("name", size, size, size, "left")

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
    _set_knob_value(name_text, "message", resolve_artist_display_name())

    # department dropdown and text
    _set_knob_value(department_text, "message", "[value departmentDropdown]")


def get_in_out():
    """
    Returns the in/out cut frames for the current shot.
    Falls back to the script's own frame range if the shot cannot be found in
    ShotGrid or is missing cut_in / cut_out there.
    """
    curr_shot = get_shot_code()
    try:
        shot = ShotGrid.connect(DB_Config).get_shot(code=curr_shot)
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


def get_shot_code():
    """A comp is `shot/<shot>/comp/<any name>.nk`, so the shot is the folder,
    not the file name."""
    return os.path.basename(os.path.dirname(os.path.dirname(nuke.root().name())))


def get_output_file_info_mov(group):
    """The next edit delivery for this shot. The MOV_write creates the folder."""
    department = DEPARTMENTS[getDepartment(group)]
    shot_code = get_shot_code()
    folder = edit_shot_directory(shot_code)
    new_file_name = next_delivery_name(folder, shot_code, department) + ".mov"
    return [new_file_name, str(folder)]


def apply_mov_path_to_ui_and_write(group):
    """Point the path label and MOV_write at the next delivery."""
    new_file_name, folder_path = get_output_file_info_mov(group)
    full_path = os.path.join(folder_path, new_file_name)
    group["mov_export_path"].setValue(full_path)
    write = group.node("MOV_write")
    write["file"].setValue(full_path)
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
    with proxy_off():
        nuke.execute(group.node("MOV_write"), first_frame, last_frame, 1)
    group[EXPORTED_KNOB].setValue(path)


@contextmanager
def proxy_off() -> Iterator[None]:
    """Proxy reads each 1080 render at half size, which would halve the movie's
    detail; the review Reformat shrinks the full frame instead."""
    root = nuke.root()
    saved = root["proxy"].value()
    root["proxy"].setValue(False)
    try:
        yield
    finally:
        root["proxy"].setValue(saved)


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


def make_review_reformat():
    """Shrinks the comp to REVIEW_HEIGHT for the movie; a 1080 comp passes through."""
    reformat = nuke.createNode("Reformat")
    _set_knob_value(reformat, "type", "scale")
    reformat["scale"].setExpression(f"{REVIEW_HEIGHT} / root.format.height")
    return reformat


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

    write_node["mov64_codec"].setValue("AVdh")

    return write_node


def check_saved():
    if not get_shot_code():
        nuke.message(
            "Save this comp as shot/<shot>/comp/<name>.nk first. "
            "The node names its movie after the shot folder."
        )
        return False
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
    first, last = get_in_out()
    frame_in = nuke.Int_Knob("export_frame_in", "")
    frame_in.setValue(first)
    frame_out = nuke.Int_Knob("export_frame_out", "")
    frame_out.setValue(last)
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
        "departmentDropdown", "Department", list(DEPARTMENTS)
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

    try:
        task_labels = [t.content for t in find_my_tasks() if t.content]
    except ShotGridError as exc:
        nuke.message(f"Could not load your ShotGrid tasks: {exc}")
        task_labels = []
    task_knob = nuke.Enumeration_Knob("shotgrid_task", "ShotGrid Task", task_labels)
    groupNode.addKnob(task_knob)

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


def find_my_tasks() -> list[Task]:
    """This shot's tasks assigned to the artist running Nuke."""
    connection = ShotGrid.connect(DB_Config)
    shot = connection.get_shot(code=get_shot_code())
    user = connection.get_user(name=resolve_artist_display_name())
    return connection.find_tasks(shot=shot, user=user)


def find_review_playlist(department: str) -> Playlist | None:
    """The newest playlist named `<M/D/YY> ...` that names `department`."""
    # A whole word, so "FX" does not pick up a CFX playlist.
    names_department = re.compile(rf"\b{re.escape(department)}\b")
    newest: Playlist | None = None
    newest_date = None
    for playlist in ShotGrid.connect(DB_Config).find_playlists(
        code_contains=department
    ):
        code = playlist.code or ""
        try:
            date = datetime.datetime.strptime(code.split(" ", 1)[0], "%m/%d/%y")
        except ValueError:
            continue
        if names_department.search(code) and (
            newest_date is None or date > newest_date
        ):
            newest, newest_date = playlist, date
    return newest


def create_new_shot_version(group):
    video_path = Path(group[EXPORTED_KNOB].value())
    if not video_path.is_file():
        nuke.message("Export the MOV before sending it to ShotGrid.")
        return

    department = getDepartment(group)
    try:
        playlist = find_review_playlist(department)
        task_name = group["shotgrid_task"].value()
        task = next((t for t in find_my_tasks() if t.content == task_name), None)
    except ShotGridError as exc:
        nuke.message(f"ShotGrid lookup failed, so nothing was sent: {exc}")
        return

    if task is None:
        nuke.message(
            f"None of your ShotGrid tasks on {get_shot_code()} is called "
            f"'{task_name}'. Pick one in ShotGrid Task, or ask your lead to "
            "assign you one."
        )
        return
    if playlist is None:
        if not nuke.ask(
            f"There is no dated {department} playlist, so this Version won't be "
            "in a dailies review. Send it anyway?"
        ):
            return
    else:
        nuke.message(
            f"Using playlist: {playlist.code}\n\n"
            "Contact your lead if you need a newer playlist!"
        )

    result = upload_playblast_version(
        PlayblastVersionUploadRequest(
            entity=ShotEntity(get_shot_code()),
            movie_path=video_path,
            # The delivery's name, so the Version and the edit file match.
            version_name=video_path.stem,
            description=group["shotgrid_description"].value() or None,
            artist_display_name=resolve_artist_display_name() or None,
            review_playlist_id=playlist.id if playlist else None,
            disk_path=video_path,
            task=task,
        )
    )
    nuke.message("\n".join([result.message, *result.warnings]))


def main():
    if check_saved():
        current_node = None
        selected_nodes = nuke.selectedNodes()
        if selected_nodes:
            current_node = selected_nodes[0]

        base_name = "SKD_Write"
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

        # An expression rather than a format, so the node follows the comp's
        # format when that changes.
        reformat_node = nuke.createNode("Reformat")
        reformat_node["type"].setValue("to box")
        reformat_node["box_fixed"].setValue(True)
        reformat_node["box_width"].setExpression("root.format.width")
        reformat_node["box_height"].setExpression("root.format.height")
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

        make_timecode_node()
        make_review_reformat()

        # MOV node
        make_MOV_node()

        # update text nodes messages
        update_text_messages(
            text_nodes[0], text_nodes[1], text_nodes[2], text_nodes[3], text_nodes[4]
        )

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

        groupNode["tile_color"].setValue(0xFF6699FF)
