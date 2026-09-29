"""What the Tractor nodes' parms say, read in one place for Send and the nodes' own LOPs."""

from __future__ import annotations

import hou

from pipe.dcc.houdini.tractor import SendRefused, paths

LAYER = "layer"
ROOT = "render_root"
# Named for the USD ROP parm inside Configure that reads it.
OUTPUT = "savetodirectory_directory"
LAST_JOB = "last_job"

RENDERER = "renderer"
SETTINGS = "rendersettings"
CAMERA = "override_camera"
RESOLUTION = "override_res"
SCALE = "scale"


def text(node: hou.Node, name: str) -> str:
    # evalParm's type covers every kind of parm.
    return str(node.evalParm(name))


def toggled(node: hou.Node, name: str) -> str:
    """A parm's value when its checkbox is on, else empty."""
    return text(node, name) if node.evalParm(f"toggle_{name}") else ""


def frames(configure: hou.LopNode) -> list[int]:
    trange = configure.parm("trange").evalAsString()  # ty: ignore[unresolved-attribute]
    if trange == "off":
        return [int(hou.frame())]
    if trange == "stage":
        stage = configure.stage()
        return list(
            range(int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode()) + 1)
        )
    start, end, step = (int(v) for v in configure.evalParmTuple("f"))
    if step < 1 or end < start:
        raise SendRefused(
            f"{configure.path()} renders frames {start} to {end} by {step}, which "
            "is no frames. Set End at or after Start, and Inc to 1 or more."
        )
    return list(range(start, end + 1, step))


def husk(configure: hou.Node) -> list[str]:
    """husk's options besides the frame and the file it renders."""
    words = [
        *("--renderer", text(configure, RENDERER)),
        *("--verbose", "acet"),
    ]
    if camera := toggled(configure, CAMERA):
        words += ["--camera", camera]
    if settings := toggled(configure, SETTINGS):
        words += ["--settings", settings]
    words.append("--disable-dummy-raster-product")
    return words


def resolution(configure: hou.Node) -> tuple[int, int]:
    """What Override Resolution renders at; unused when it is None.

    Configure's LOPs set it on every render settings and every render product,
    since husk renders a product's own resolution first.
    """
    if configure.evalParm(RESOLUTION) != SCALE:
        x, y = configure.evalParmTuple("res_user")
        return int(x), int(y)
    stage = configure.inputs()[0].stage()  # ty: ignore[unresolved-attribute]
    settings = paths.rendered_settings(stage, toggled(configure, SETTINGS))
    x, y = paths.resolution(settings, paths.products(settings))
    scale = int(configure.evalParm("res_scale")) / 100
    return int(x * scale), int(y * scale)
