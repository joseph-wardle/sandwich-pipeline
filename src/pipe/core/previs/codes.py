"""Sticky shot codes

A sticky code (``A_010``) is a shot's sequence-level identity. Artists declare
codes them manually. Our code parses, puts them in canonical padded form, and
offers unused-number suggestions the UI can pre-fill.
"""

from __future__ import annotations

import re
from typing import Iterable

SHOT_CODE_RE = re.compile(r"^(?P<letter>[A-Z])_(?P<number>\d+)$")
STEP = 10
PAD = 3


def parse_code(code: str) -> tuple[str, int] | None:
    """Return ``(letter, number)`` for a valid code, else ``None`` (never raises)."""
    match = SHOT_CODE_RE.match(code)
    if match is None:
        return None
    return match.group("letter"), int(match.group("number"))


def format_code(letter: str, number: int) -> str:
    return f"{letter}_{number:0{PAD}d}"


def normalize_code(code: str) -> str:
    """Return the code in canonical padded form; raise ``ValueError`` if malformed.

    Canonicalizing on the write path stops ``A_10`` and ``A_010`` from becoming
    two manifest keys for what an artist means as one shot.
    """
    parsed = parse_code(code)
    if parsed is None:
        raise ValueError(
            f"{code!r} is not a valid shot code "
            "(expected <LETTER>_<number>, e.g. 'A_010')"
        )
    return format_code(*parsed)


def is_taken(code: str, existing: Iterable[str]) -> bool:
    return code in set(existing)


def suggest_next(letter: str, existing: Iterable[str]) -> str:
    """Suggest the next free code for ``letter``: highest used number + STEP."""
    numbers = _numbers_for_letter(letter, existing)
    following = (max(numbers) + STEP) if numbers else STEP
    return format_code(letter, following)


def suggest_midpoint(before: str, after: str) -> str | None:
    """Suggest a code between two existing ones, or ``None`` if there is no gap.

    Returns ``None`` when the codes differ in letter or have no integer strictly
    between them (e.g. ``A_010``/``A_011``);
    """
    before_parsed = parse_code(before)
    after_parsed = parse_code(after)
    if before_parsed is None or after_parsed is None:
        return None
    letter, low = before_parsed
    after_letter, high = after_parsed
    if letter != after_letter or high - low < 2:
        return None
    return format_code(letter, (low + high) // 2)


def _numbers_for_letter(letter: str, existing: Iterable[str]) -> list[int]:
    numbers: list[int] = []
    for code in existing:
        parsed = parse_code(code)
        if parsed is not None and parsed[0] == letter:
            numbers.append(parsed[1])
    return numbers


__all__ = [
    "SHOT_CODE_RE",
    "STEP",
    "PAD",
    "parse_code",
    "format_code",
    "normalize_code",
    "is_taken",
    "suggest_next",
    "suggest_midpoint",
]
