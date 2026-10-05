"""Callbacks of the BrushCard group, which `build_brush_card.build()` creates.

Every card ends up in one USD stage, so each needs a prim path of its own. Nuke
also names a card's material prim after the node feeding the GeoCard's material
input, so cards whose texture nodes share a name all render the same stroke.
`sync_names()` derives both from the group's name.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

import nuke

from pipe.core.util.paths import get_production_path

KnobT = TypeVar("KnobT", bound=nuke.Knob)

LIBRARY_ENV_VAR = "BRUSH_CARD_LIB"
STROKE_EXTENSIONS = (".exr", ".png", ".tif", ".tiff")
PLACEHOLDER = "<no strokes found>"
PRIM_ROOT = "/BrushCards"

CARD_NODE = "Card"
# By class, because the names follow the group's.
TEXTURE_NODE_SUFFIXES = {"Read": "_Read", "Premult": "_Premult"}
STROKE_KNOB = "stroke"


def library_dir() -> Path:
    value = os.environ.get(LIBRARY_ENV_VAR)
    if value:
        return Path(value)
    return get_production_path() / "lighting" / "crepuscular_cards"


def strokes() -> dict[str, str]:
    """{display name: file path} of every stroke in the library."""
    lib = library_dir()
    if not lib.is_dir():
        return {}
    return {
        f.stem: f.as_posix()
        for f in sorted(lib.iterdir())
        if f.is_file() and f.suffix.lower() in STROKE_EXTENSIONS
    }


def _this_group() -> nuke.Group:
    node = nuke.thisNode()
    if not isinstance(node, nuke.Group):
        raise TypeError(f"BrushCard callback ran on {node.Class()} {node.name()!r}")
    return node


def _knob(node: nuke.Node, name: str, kind: type[KnobT]) -> KnobT:
    knob = node[name]
    if not isinstance(knob, kind):
        raise TypeError(
            f"{node.name()}.{name} is {type(knob).__name__}, expected {kind.__name__}"
        )
    return knob


def _stroke_knob(group: nuke.Group) -> nuke.Enumeration_Knob:
    return _knob(group, STROKE_KNOB, nuke.Enumeration_Knob)


def _internal(group: nuke.Group, node_class: str) -> nuke.Node | None:
    for node in group.nodes():
        if node.Class() == node_class:
            return node
    return None


def _read(group: nuke.Group) -> nuke.Node | None:
    return _internal(group, "Read")


def set_stroke(group: nuke.Group) -> None:
    """Point the Read at the chosen stroke.

    With no stroke the GeoCard is disabled, not the Read: a card without a
    texture falls back to an opaque material and renders a rectangle.
    """
    read = _read(group)
    card = group.node(CARD_NODE)
    if read is None or card is None:
        return
    path = strokes().get(_stroke_knob(group).value())
    if path is not None:
        _knob(read, "file", nuke.File_Knob).fromUserText(path)
        read["before"].setValue("hold")
        read["after"].setValue("hold")
    # A stroke removed from the library keeps its last file, so a saved comp
    # doesn't change.
    has_stroke = bool(read["file"].value())
    read["disable"].setValue(False)
    card["disable"].setValue(not has_stroke)


def refresh(group: nuke.Group | None = None) -> None:
    group = group or _this_group()
    stroke = _stroke_knob(group)
    found = list(strokes())
    current = stroke.value()

    names = found or [PLACEHOLDER]
    # A stroke removed from the library stays selected, as in `set_stroke`.
    if current and current != PLACEHOLDER and current not in names:
        names.append(current)

    stroke.setValues(names)
    stroke.setValue(current if current in names else names[0])
    set_stroke(group)

    if not found and nuke.GUI:
        nuke.message(
            f"BrushCard: no strokes found in\n{library_dir()}\n\n"
            f"Add {', '.join(STROKE_EXTENSIONS)} files there, or point the "
            f"{LIBRARY_ENV_VAR} environment variable at the stroke library."
        )


def sync_names(group: nuke.Group) -> None:
    card = group.node(CARD_NODE)
    if card is not None:
        path = f"{PRIM_ROOT}/{group.name()}"
        if card["prim_path"].value() != path:
            card["prim_path"].setValue(path)

    for node_class, suffix in TEXTURE_NODE_SUFFIXES.items():
        node = _internal(group, node_class)
        if node is not None and node.name() != group.name() + suffix:
            node.setName(group.name() + suffix)


def on_create() -> None:
    """Also runs when the group is pasted or loaded from a script."""
    group = _this_group()
    sync_names(group)
    read = _read(group)
    if read is not None and not read["file"].value():
        refresh(group)


def on_knob_changed() -> None:
    group = _this_group()
    knob = nuke.thisKnob().name()
    if knob == STROKE_KNOB:
        set_stroke(group)
    elif knob == "name":
        sync_names(group)
