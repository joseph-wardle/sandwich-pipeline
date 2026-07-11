from __future__ import annotations

import pytest

from pipe.core.previs import codes


def test_parse_code_valid() -> None:
    assert codes.parse_code("A_010") == ("A", 10)
    assert codes.parse_code("G_250") == ("G", 250)


@pytest.mark.parametrize("bad", ["", "A010", "a_010", "A_", "AB_010", "A_01x", "1_010"])
def test_parse_code_invalid_returns_none(bad: str) -> None:
    assert codes.parse_code(bad) is None


def test_format_code_pads_to_three() -> None:
    assert codes.format_code("A", 10) == "A_010"
    assert codes.format_code("A", 5) == "A_005"
    # Numbers wider than the padding are kept whole, not truncated.
    assert codes.format_code("A", 1200) == "A_1200"


def test_normalize_code_canonicalizes_padding() -> None:
    assert codes.normalize_code("A_10") == "A_010"
    assert codes.normalize_code("A_010") == "A_010"


def test_normalize_code_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        codes.normalize_code("nope")


def test_suggest_next_on_empty_starts_at_step() -> None:
    assert codes.suggest_next("A", []) == "A_010"


def test_suggest_next_appends_after_max() -> None:
    assert codes.suggest_next("A", ["A_010", "A_030", "A_020"]) == "A_040"


def test_suggest_next_ignores_other_letters() -> None:
    assert codes.suggest_next("B", ["A_090", "A_100"]) == "B_010"


def test_suggest_midpoint_finds_gap() -> None:
    assert codes.suggest_midpoint("A_010", "A_020") == "A_015"


def test_suggest_midpoint_no_integer_gap_returns_none() -> None:
    assert codes.suggest_midpoint("A_010", "A_011") is None


def test_suggest_midpoint_different_letters_returns_none() -> None:
    assert codes.suggest_midpoint("A_010", "B_020") is None


def test_suggest_midpoint_malformed_returns_none() -> None:
    assert codes.suggest_midpoint("A_010", "junk") is None
