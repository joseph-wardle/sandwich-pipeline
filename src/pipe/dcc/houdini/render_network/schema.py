"""The Render Network's parm templates and parm states as plain data, and edits to them.

Templates keep Houdini's own type names, such as "Toggle" and "Collapsible".
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import hou

from pipe.core.render.protocol import EXPRESSION, LANGUAGE, ParmState

HEADER_TOGGLE_TAG = "sidefx::header_toggle"
LOOK_TAG = "sidefx::look"
# Solaris marks parms that take prim paths, which the window offers a picker for.
USD_PATH_TAG = "sidefx::usdpathtype"
START_OFFSET_TAG = "multistartoffset"

_LANGUAGES = {
    language.name(): language
    for language in (hou.exprLanguage.Hscript, hou.exprLanguage.Python)
}


def describe(node: hou.Node) -> list[dict[str, Any]]:
    """The node's parm templates, in the order Houdini draws them."""
    return [_entry(t) for t in _visible(node.parmTemplateGroup().entries())]


def states(node: hou.Node) -> dict[str, ParmState]:
    # Headless Houdini evaluates Disable When and Hide When only when asked.
    node.updateParmStates()
    return {
        parm_tuple.name(): ParmState(
            value=[_value(p) for p in parm_tuple],
            evaluated=[_evaluated(p) for p in parm_tuple],
            disabled=parm_tuple.isDisabled(),
            hidden=parm_tuple.isHidden(),
        )
        for parm_tuple in node.parmTuples()
    }


def set_parm(node: hou.Node, name: str, value: list[Any]) -> None:
    if (parm_tuple := node.parmTuple(name)) is None:
        raise ValueError(f"{node.path()} has no parm {name}")
    for parm, component in zip(parm_tuple, value):
        if isinstance(component, dict):
            parm.setExpression(component[EXPRESSION], _LANGUAGES[component[LANGUAGE]])
            continue
        if parm.keyframes():
            parm.deleteAllKeyframes()
        parm.set(int(component) if isinstance(component, bool) else component)


def remove_instance(node: hou.Node, name: str, position: int) -> None:
    """Remove the multiparm's instance at `position`, counting from 0."""
    if (parm := node.parm(name)) is None:
        raise ValueError(f"{node.path()} has no parm {name}")
    parm.removeMultiParmInstance(position)


def _visible(templates: Sequence[hou.ParmTemplate]) -> Iterator[hou.ParmTemplate]:
    return (t for t in templates if not t.isHidden())


def _entry(template: hou.ParmTemplate) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": template.name(),
        "label": template.label(),
        "help": template.help(),
        "type": template.type().name(),
    }
    tags = template.tags()
    if isinstance(template, hou.FolderParmTemplate):
        return {
            **entry,
            "folder_type": template.folderType().name(),
            "header_toggle": tags.get(HEADER_TOGGLE_TAG, ""),
            # "blank" draws no frame, as for each multiparm instance's group.
            "look": tags.get(LOOK_TAG, ""),
            "start": int(tags.get(START_OFFSET_TAG, "1")),
            "children": [_entry(t) for t in _visible(template.parmTemplates())],
        }
    entry.update(
        size=template.numComponents(),
        join_next=template.joinsWithNext(),
        no_label=template.isLabelHidden(),
        prim_path=USD_PATH_TAG in tags,
    )
    if isinstance(template, hou.MenuParmTemplate):
        entry["menu"] = list(zip(template.menuItems(), template.menuLabels()))
    elif isinstance(template, hou.LabelParmTemplate):
        entry["text"] = "\n".join(template.columnLabels())
    return entry


def _value(parm: hou.Parm) -> Any:
    if parm.keyframes():
        return {
            EXPRESSION: parm.expression(),
            LANGUAGE: parm.expressionLanguage().name(),
        }
    if parm.parmTemplate().type() == hou.parmTemplateType.String:
        return parm.unexpandedString()
    return _evaluated(parm)


def _evaluated(parm: hou.Parm) -> Any:
    kind = parm.parmTemplate().type()
    if kind == hou.parmTemplateType.Toggle:
        return bool(parm.eval())
    if kind in (hou.parmTemplateType.Menu, hou.parmTemplateType.String):
        return parm.evalAsString()
    value = parm.eval()
    # Such as a ramp, which the window doesn't draw.
    return value if isinstance(value, (int, float, str)) else str(value)
