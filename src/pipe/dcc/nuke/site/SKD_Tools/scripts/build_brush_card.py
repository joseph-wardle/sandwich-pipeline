"""Builds a BrushCard group: a GeoCard textured with a stroke from the library."""

from __future__ import annotations

import nuke

import brush_card

GROUP_NAME = "BrushCard1"
PREMULT_KNOB = "premult"

GEOCARD_SCENE_INPUT = 0
GEOCARD_MATERIAL_INPUT = 1

# Shot scenes are in centimeters, so a new card is 1 m wide.
DEFAULT_UNIFORM_SCALE = 100.0

# A missing module is tolerated so a saved comp still loads and renders where
# SKD_Tools is not on the path, such as `nuke -t` on the farm.
ON_CREATE = """try:
    import brush_card
except ImportError:
    pass
else:
    brush_card.on_create()
"""
KNOB_CHANGED = """try:
    import brush_card
except ImportError:
    pass
else:
    brush_card.on_knob_changed()
"""
REFRESH = "import brush_card\nbrush_card.refresh()"

LINKED_TRANSFORM_KNOBS = (
    ("translate", "translate"),
    ("rotate", "rotate"),
    ("scaling", "scale"),
    ("uniform_scale", "uniform scale"),
)


def build() -> nuke.Group:
    group = nuke.nodes.Group()
    group.setName(GROUP_NAME, uncollide=True)
    group["tile_color"].setValue(0x6B4F8FFF)

    _build_internals(group)
    _add_user_knobs(group)
    group["onCreate"].setValue(ON_CREATE)
    group["knobChanged"].setValue(KNOB_CHANGED)

    # onCreate fired before the internals existed.
    brush_card.sync_names(group)
    brush_card.refresh(group)
    return group


def _build_internals(group: nuke.Group) -> None:
    with group:
        read = nuke.nodes.Read()
        read.setXYpos(0, 0)

        premult = nuke.nodes.Premult()
        premult.setInput(0, read)
        premult["disable"].setExpression(f"!parent.{PREMULT_KNOB}")
        premult.setXYpos(0, 120)

        scene = nuke.nodes.Input(name="scene")
        scene.setXYpos(-150, 200)

        card = nuke.nodes.GeoCard(name=brush_card.CARD_NODE)
        card.setInput(GEOCARD_SCENE_INPUT, scene)
        card.setInput(GEOCARD_MATERIAL_INPUT, premult)
        card["uniform_scale"].setValue(DEFAULT_UNIFORM_SCALE)
        card.setXYpos(0, 200)

        output = nuke.nodes.Output()
        output.setInput(0, card)
        output.setXYpos(0, 300)


def _add_user_knobs(group: nuke.Group) -> None:
    group.addKnob(nuke.Tab_Knob("brush_card", "BrushCard"))

    stroke = nuke.Enumeration_Knob(
        brush_card.STROKE_KNOB, "stroke", [brush_card.PLACEHOLDER]
    )
    stroke.setTooltip(f"Stroke image from the library (${brush_card.LIBRARY_ENV_VAR}).")
    group.addKnob(stroke)

    refresh = nuke.PyScript_Knob("refresh", "Refresh", REFRESH)
    refresh.setTooltip("Rescan the stroke library for new or removed files.")
    group.addKnob(refresh)

    premult = nuke.Boolean_Knob(PREMULT_KNOB, "premultiply stroke")
    premult.setValue(True)
    premult.setTooltip(
        "Premultiply the stroke before texturing. Leave on for straight-alpha "
        "images (e.g. PNG); turn off if the library files are premultiplied."
    )
    premult.setFlag(nuke.STARTLINE)
    group.addKnob(premult)

    group.addKnob(nuke.Text_Knob("transform_divider", "transform"))
    for knob, label in LINKED_TRANSFORM_KNOBS:
        link = nuke.Link_Knob(knob, label)
        link.setLink(f"{brush_card.CARD_NODE}.{knob}")
        group.addKnob(link)
