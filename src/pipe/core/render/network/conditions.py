"""Houdini's Disable When and Hide When conditions, evaluated outside Houdini.

A condition is one or more `{ ... }` groups, and holds when any group does. A
group holds when every comparison in it does:

    { enableBeauty == 0 }                       one comparison
    { framerange == off } { framerange == stage }   either
    { a == 1 b != 0 }                           both

A comparison names a parm, an operator and a value. A menu compares by token
and by index; a toggle compares as 0 or 1.
"""

from __future__ import annotations

import operator
import re
import shlex
from collections.abc import Callable, Mapping
from typing import Any

# A parm the window doesn't know, such as one of the asset's inner nodes that
# its warning labels read. Callers choose what a comparison of one means: Hide
# When treats it as holding, so those labels stay hidden rather than always
# showing their warning.
UNKNOWN = object()

_GROUP = re.compile(r"\{([^}]*)\}")
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
    "=~": lambda value, pattern: re.search(str(pattern), str(value)) is not None,
}

Lookup = Callable[[str], Any]


def holds(condition: str, lookup: Lookup, if_unknown: bool) -> bool:
    """Whether `condition` holds, reading each parm's value with `lookup`.

    `lookup` returns UNKNOWN for a parm it doesn't know; a comparison of one is
    `if_unknown`. An empty condition never holds.
    """
    groups = _GROUP.findall(condition)
    return any(_group_holds(group, lookup, if_unknown) for group in groups)


def _group_holds(group: str, lookup: Lookup, if_unknown: bool) -> bool:
    try:
        words = shlex.split(group)
    except ValueError:
        return False
    if not words or len(words) % 3:
        return False
    for index in range(0, len(words), 3):
        name, symbol, expected = words[index : index + 3]
        compare = _OPERATORS.get(symbol)
        if compare is None:
            return False
        value = lookup(name)
        if value is UNKNOWN:
            if not if_unknown:
                return False
            continue
        if not _compare(compare, value, expected):
            return False
    return True


def _compare(compare: Callable[[Any, Any], bool], value: Any, expected: str) -> bool:
    if isinstance(value, MenuValue):
        if _is_number(expected):
            return compare(value.index, float(expected))
        return compare(value.token, expected)
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)) and _is_number(expected):
        return compare(float(value), float(expected))
    # Houdini reads a toggle's "on" and "off" as 1 and 0.
    if isinstance(value, (int, float)) and expected in ("on", "off"):
        return compare(float(value), 1.0 if expected == "on" else 0.0)
    return compare(str(value), expected)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


class MenuValue:
    """A menu parm's value, which conditions compare by token or by index."""

    token: str
    index: int

    def __init__(self, token: str, tokens: list[str]) -> None:
        self.token = token
        self.index = tokens.index(token) if token in tokens else -1


def lookup_in(values: Mapping[str, Any]) -> Lookup:
    """A lookup over `values`, returning UNKNOWN for a name it lacks."""
    return lambda name: values.get(name, UNKNOWN)
