import os
import nuke


LIGHTS_PREFIX = "/lights/"
USD_RELATIVE = os.path.join("..", "lighting", "usd", "main.usd")

BRANCH_SPACING = 120
SHUFFLE_Y_OFFSET = 200
GRADE_Y_OFFSET = 50
MERGE_Y_OFFSET = 80


def _light_layer_name(prim_path):
    s = str(prim_path)
    if s.startswith(LIGHTS_PREFIX):
        s = s[len(LIGHTS_PREFIX) :]
    else:
        s = s.lstrip("/")
    return s.replace("/", "__")


def _collect_light_layer_names(usd_path):
    from pxr import Usd, UsdLux

    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise RuntimeError("Could not open USD: %s" % usd_path)
    names = []
    for prim in stage.Traverse():
        if prim.HasAPI(UsdLux.LightAPI):  # type: ignore
            names.append(_light_layer_name(prim.GetPath()))
    return names


def _resolve_usd_path():
    script = nuke.root().name()
    if script == "Root":
        raise RuntimeError("Save the Nuke script before running this tool.")
    script_dir = os.path.dirname(script)
    return os.path.normpath(os.path.join(script_dir, USD_RELATIVE))


def run():
    selected = nuke.selectedNodes()
    if len(selected) != 1 or selected[0].Class() != "Read":
        nuke.message("Select a single Read node first.")
        return
    read = selected[0]

    try:
        usd_path = _resolve_usd_path()
    except RuntimeError as e:
        nuke.message(str(e))
        return

    if not os.path.exists(usd_path):
        nuke.message("USD not found at:\n%s" % usd_path)
        return

    try:
        layer_names = sorted(_collect_light_layer_names(usd_path))
    except Exception as e:
        nuke.message("USD parse failed:\n%s" % e)
        return

    if not layer_names:
        nuke.message("No light prims found in:\n%s" % usd_path)
        return

    base_x = read.xpos()
    base_y = read.ypos()
    shuffle_y = base_y + SHUFFLE_Y_OFFSET
    grade_y = shuffle_y + GRADE_Y_OFFSET
    merge_y = grade_y + MERGE_Y_OFFSET

    for n in nuke.selectedNodes():
        n.setSelected(False)

    grades = []
    for i, layer in enumerate(layer_names):
        nuke.Layer(
            layer,
            [
                "%s.red" % layer,
                "%s.green" % layer,
                "%s.blue" % layer,
            ],
        )

        x = base_x + i * BRANCH_SPACING

        shuffle = nuke.nodes.Shuffle2(name="Shuffle_%s" % layer, xpos=x, ypos=shuffle_y)
        shuffle.setInput(0, read)
        shuffle["in1"].setValue(layer)

        grade = nuke.nodes.Grade(name="%s_grade" % layer, xpos=x, ypos=grade_y)
        grade.setInput(0, shuffle)
        grades.append(grade)

    merge_x = base_x + ((len(grades) - 1) * BRANCH_SPACING) // 2
    merge = nuke.nodes.Merge2(
        name="Merge_lights",
        operation="plus",
        xpos=merge_x,
        ypos=merge_y,
    )
    for i, g in enumerate(grades):
        target = i if i < 2 else i + 1
        merge.setInput(target, g)

    merge.setSelected(True)
