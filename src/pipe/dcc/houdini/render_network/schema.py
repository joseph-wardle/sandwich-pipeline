"""The Render Network's parm interface as plain data, and its values in and out.

The window draws whatever the asset's parm templates describe, so a parm added
to the HDA shows up in the window without changing the window.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import hou

from pipe.core.render.protocol import (
    BUTTON,
    COLLAPSIBLE,
    EXPRESSION,
    EXPRESSION_LANGUAGE,
    FLOAT,
    FOLDER,
    HSCRIPT,
    INSTANCE_TOKEN,
    INT,
    LABEL,
    MENU,
    MULTIPARM,
    PARM,
    PYTHON,
    SEPARATOR,
    SIMPLE,
    STRING,
    TABS,
    TOGGLE,
    Values,
)

HEADER_TOGGLE_TAG = "sidefx::header_toggle"
LOOK_TAG = "sidefx::look"
# Solaris marks parms that take prim paths, which the window offers a picker for.
USD_PATH_TAG = "sidefx::usdpathtype"
# Houdini numbers multiparm instances from 1 unless the multiparm says otherwise.
START_OFFSET_TAG = "multistartoffset"

_FOLDER_STYLES = {
    hou.folderType.Tabs: TABS,
    hou.folderType.RadioButtons: TABS,
    hou.folderType.Collapsible: COLLAPSIBLE,
    hou.folderType.Simple: SIMPLE,
}
_MULTIPARMS = (
    hou.folderType.MultiparmBlock,
    hou.folderType.ScrollingMultiparmBlock,
    hou.folderType.TabbedMultiparmBlock,
)
_PARM_TYPES = {
    hou.parmTemplateType.Toggle: TOGGLE,
    hou.parmTemplateType.String: STRING,
    hou.parmTemplateType.Int: INT,
    hou.parmTemplateType.Float: FLOAT,
    hou.parmTemplateType.Menu: MENU,
    hou.parmTemplateType.Label: LABEL,
    hou.parmTemplateType.Button: BUTTON,
    hou.parmTemplateType.Separator: SEPARATOR,
}
_LANGUAGES = {hou.exprLanguage.Hscript: HSCRIPT, hou.exprLanguage.Python: PYTHON}
_LANGUAGE_OF = {name: language for language, name in _LANGUAGES.items()}
# Parm templates name their default expression's language with another enum.
_TEMPLATE_LANGUAGES = {
    hou.scriptLanguage.Hscript: HSCRIPT,
    hou.scriptLanguage.Python: PYTHON,
}
# Types that hold no value the window edits.
_VALUELESS = (LABEL, BUTTON, SEPARATOR)


def describe(node: hou.Node) -> list[dict[str, Any]]:
    """The node's parm interface, in the order Houdini draws it."""
    return [_entry(t) for t in _visible(node.parmTemplateGroup().entries())]


def read_values(node: hou.Node, schema: list[dict[str, Any]]) -> Values:
    """Every value the schema's parms hold on `node`, keyed by parm tuple name."""
    values: Values = {}
    for name, entry in _value_parms(node, schema):
        if entry["kind"] == MULTIPARM:
            values[name] = [_parm(node, name).evalAsInt()]
        else:
            values[name] = [_read(p, entry["type"]) for p in _parm_tuple(node, name)]
    return values


def evaluate_expressions(node: hou.Node, schema: list[dict[str, Any]]) -> Values:
    """What each parm tuple that an expression drives evaluates to now."""
    evaluated: Values = {}
    for name, entry in _value_parms(node, schema):
        if entry["kind"] == MULTIPARM:
            continue
        parm_tuple = _parm_tuple(node, name)
        if any(p.keyframes() for p in parm_tuple):
            evaluated[name] = [p.eval() for p in parm_tuple]
    return evaluated


def apply_values(node: hou.Node, schema: list[dict[str, Any]], values: Values) -> None:
    """Set the schema's parms on `node` from `values`, leaving parms it leaves out.

    Walks in schema order, so each multiparm has its instances before their
    parms are set.
    """
    for name, entry in _value_parms(node, schema, values):
        if name not in values:
            continue
        if entry["kind"] == MULTIPARM:
            _parm(node, name).set(int(values[name][0]))
            continue
        for parm, value in zip(_parm_tuple(node, name), values[name]):
            _write(parm, value)


def _visible(templates: Sequence[hou.ParmTemplate]) -> Iterator[hou.ParmTemplate]:
    return (t for t in templates if not t.isHidden())


def _entry(template: hou.ParmTemplate) -> dict[str, Any]:
    conditionals = template.conditionals()
    common = {
        "name": template.name(),
        "label": template.label(),
        "help": template.help(),
        "disable_when": conditionals.get(hou.parmCondType.DisableWhen, ""),
        "hide_when": conditionals.get(hou.parmCondType.HideWhen, ""),
    }
    if isinstance(template, hou.FolderParmTemplate):
        children = [_entry(t) for t in _visible(template.parmTemplates())]
        if template.folderType() in _MULTIPARMS:
            start = int(template.tags().get(START_OFFSET_TAG, "1"))
            return {**common, "kind": MULTIPARM, "start": start, "children": children}
        return {
            **common,
            "kind": FOLDER,
            "style": _FOLDER_STYLES.get(template.folderType(), SIMPLE),
            "header_toggle": template.tags().get(HEADER_TOGGLE_TAG, ""),
            # "blank" draws no frame, as for each multiparm instance's group.
            "look": template.tags().get(LOOK_TAG, ""),
            "children": children,
        }
    return {**common, "kind": PARM, **_parm_fields(template)}


def _parm_fields(template: hou.ParmTemplate) -> dict[str, Any]:
    kind = _PARM_TYPES.get(template.type(), LABEL)
    fields: dict[str, Any] = {
        "type": kind,
        "size": template.numComponents(),
        "join_next": template.joinsWithNext(),
        "no_label": template.isLabelHidden(),
        "prim_path": USD_PATH_TAG in template.tags(),
    }
    fields["default"] = _defaults(template, kind)
    if isinstance(template, hou.MenuParmTemplate):
        fields["menu"] = list(zip(template.menuItems(), template.menuLabels()))
    elif isinstance(template, hou.LabelParmTemplate):
        fields["text"] = "\n".join(template.columnLabels())
    elif isinstance(template, (hou.IntParmTemplate, hou.FloatParmTemplate)):
        fields["min"] = template.minValue()
        fields["max"] = template.maxValue()
        fields["min_strict"] = template.minIsStrict()
        fields["max_strict"] = template.maxIsStrict()
    return fields


def _defaults(template: hou.ParmTemplate, kind: str) -> list[Any]:
    """A new multiparm instance's values, one per component."""
    if kind in _VALUELESS:
        return []
    if isinstance(template, hou.MenuParmTemplate):
        items = template.menuItems()
        index = template.defaultValue()
        return [items[index] if 0 <= index < len(items) else ""]
    if isinstance(template, hou.ToggleParmTemplate):
        return [bool(template.defaultValue())]
    if not isinstance(
        template, (hou.IntParmTemplate, hou.FloatParmTemplate, hou.StringParmTemplate)
    ):
        return []
    values = list(template.defaultValue())
    expressions = list(template.defaultExpression())
    languages = list(template.defaultExpressionLanguage())
    return [
        {EXPRESSION: expr, EXPRESSION_LANGUAGE: _TEMPLATE_LANGUAGES[language]}
        if expr
        else value
        for value, expr, language in zip(values, expressions, languages)
    ]


def _value_parms(
    node: hou.Node, schema: list[dict[str, Any]], values: Values | None = None
) -> Iterator[tuple[str, dict[str, Any]]]:
    """(parm tuple name, schema entry) of every parm that holds a value, with
    each multiparm before its instances' parms.

    A multiparm's instances are those `values` asks for when given, else those
    `node` holds now.
    """
    for entry in schema:
        yield from _walk(node, entry, values, "")


def _walk(
    node: hou.Node,
    entry: dict[str, Any],
    values: Values | None,
    instance: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    name = (
        entry["name"].replace(INSTANCE_TOKEN, instance, 1)
        if instance
        else entry["name"]
    )
    if entry["kind"] == FOLDER:
        for child in entry["children"]:
            yield from _walk(node, child, values, instance)
    elif entry["kind"] == MULTIPARM:
        yield name, entry
        if values is not None and name in values:
            count = int(values[name][0])
        else:
            count = _parm(node, name).evalAsInt()
        for index in range(entry["start"], entry["start"] + count):
            for child in entry["children"]:
                yield from _walk(node, child, values, str(index))
    elif entry["type"] not in _VALUELESS:
        yield name, entry


def _parm(node: hou.Node, name: str) -> hou.Parm:
    if (parm := node.parm(name)) is None:
        raise ValueError(f"{node.path()} has no parm {name}")
    return parm


def _parm_tuple(node: hou.Node, name: str) -> hou.ParmTuple:
    if (parm_tuple := node.parmTuple(name)) is None:
        raise ValueError(f"{node.path()} has no parm {name}")
    return parm_tuple


def _read(parm: hou.Parm, kind: str) -> Any:
    if parm.keyframes():
        language = _LANGUAGES[parm.expressionLanguage()]
        return {EXPRESSION: parm.expression(), EXPRESSION_LANGUAGE: language}
    if kind == TOGGLE:
        return bool(parm.eval())
    if kind == MENU:
        return parm.evalAsString()
    if kind == STRING:
        return parm.unexpandedString()
    return parm.eval()


def _write(parm: hou.Parm, value: Any) -> None:
    if isinstance(value, dict):
        language = value.get(EXPRESSION_LANGUAGE, HSCRIPT)
        parm.setExpression(value[EXPRESSION], _LANGUAGE_OF[language])
        return
    if parm.keyframes():
        parm.deleteAllKeyframes()
    if isinstance(value, bool):
        value = int(value)
    parm.set(value)
