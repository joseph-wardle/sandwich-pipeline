from __future__ import annotations

import re

NATURAL_SORT_REGEX = re.compile(r"(\d+)")


def natural_sort_key(value: str) -> tuple[int | str, ...]:
    """Natural string sort key.

    Examples:
        joint1 < joint2 < joint10
        apple < banana < carrot
    """
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in NATURAL_SORT_REGEX.split(value)
        if part
    )


def and_list(items: list[str]) -> str:
    """'CFX', 'CFX and FX', 'CFX, FX and Lighting'."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
