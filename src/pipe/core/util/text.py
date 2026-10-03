from __future__ import annotations


def and_list(items: list[str]) -> str:
    """'CFX', 'CFX and FX', 'CFX, FX and Lighting'."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
