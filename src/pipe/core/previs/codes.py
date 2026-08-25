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
    return format_code(*_require_parsed(code))


def shot_letter(code: str) -> str:
    """The sequence a shot code belongs to (``A_020`` → ``A``); raise if malformed."""
    return _require_parsed(code)[0]


def _require_parsed(code: str) -> tuple[str, int]:
    parsed = parse_code(code)
    if parsed is None:
        raise ValueError(
            f"{code!r} is not a valid shot code "
            "(expected <LETTER>_<number>, e.g. 'A_010')"
        )
    return parsed


def suggest_next(letter: str, existing: Iterable[str]) -> str:
    """Suggest the next free code for ``letter``: highest used number + STEP."""
    numbers = _numbers_for_letter(letter, existing)
    following = (max(numbers) + STEP) if numbers else STEP
    return format_code(letter, following)


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
    "shot_letter",
    "suggest_next",
]
