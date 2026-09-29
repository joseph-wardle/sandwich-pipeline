"""Configure's Override Resolution, which its LOPs set on every render settings
and every render product, since husk renders a product's own resolution first.
"""

from __future__ import annotations

import hou

from pipe.dcc.houdini.tractor import paths

OVERRIDE = "override_res"
SCALE = "scale"


def override(configure: hou.Node) -> tuple[int, int]:
    """What Override Resolution renders at; unused when it is None."""
    if configure.evalParm(OVERRIDE) != SCALE:
        x, y = configure.evalParmTuple("res_user")
        return int(x), int(y)
    stage = configure.inputs()[0].stage()  # ty: ignore[unresolved-attribute]
    path = configure.evalParm("toggle_rendersettings") and configure.evalParm(
        "rendersettings"
    )
    settings = paths.rendered_settings(stage, str(path or ""))
    x, y = paths.resolution(settings, paths.products(settings))
    scale = int(configure.evalParm("res_scale")) / 100
    return int(x * scale), int(y * scale)
