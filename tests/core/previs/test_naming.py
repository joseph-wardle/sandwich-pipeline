from __future__ import annotations

import pytest

from pipe.core.previs import naming


# --- sequence_letter ---------------------------------------------------------


def test_sequence_letter_of_plain_sequence() -> None:
    assert naming.sequence_letter("A_previs") == "A"


def test_sequence_letter_of_split_sequence() -> None:
    assert naming.sequence_letter("G_previs_2") == "G"


@pytest.mark.parametrize(
    "bad",
    ["A_010", "a_previs", "AB_previs", "previs", "", "A"],
)
def test_sequence_letter_rejects_non_sequence_codes(bad: str) -> None:
    with pytest.raises(ValueError):
        naming.sequence_letter(bad)


# --- normalize_label ---------------------------------------------------------


def test_normalize_label_passes_canonical_through() -> None:
    assert naming.normalize_label("blocking") == "blocking"


def test_normalize_label_folds_case() -> None:
    assert naming.normalize_label("Blocking") == "blocking"


def test_normalize_label_strips_surrounding_space() -> None:
    assert naming.normalize_label("  acting  ") == "acting"


def test_normalize_label_allows_digits() -> None:
    assert naming.normalize_label("acting2") == "acting2"


def test_normalize_label_preserves_underscores() -> None:
    assert naming.normalize_label("layout_pass") == "layout_pass"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("layout pass", "layout_pass"),
        ("layout   pass", "layout_pass"),  # a run of separators collapses to one
        ("layout-pass", "layout_pass"),
        ("layout.pass", "layout_pass"),
        ("acting/v2", "acting_v2"),  # a path separator can never escape the label
        ("  _Layout Pass_  ", "layout_pass"),  # trims leading/trailing underscores
        ("blöck", "bl_ck"),  # non-ASCII letters count as separators
    ],
)
def test_normalize_label_slugifies_separators(raw: str, expected: str) -> None:
    assert naming.normalize_label(raw) == expected


@pytest.mark.parametrize("blank", ["", "   ", "___", "!!!", "  //  "])
def test_normalize_label_rejects_no_usable_characters(blank: str) -> None:
    with pytest.raises(ValueError):
        naming.normalize_label(blank)


# --- version_token -----------------------------------------------------------


def test_version_token_pads_to_three_digits() -> None:
    assert naming.version_token(1) == "v001"
    assert naming.version_token(42) == "v042"


def test_version_token_does_not_truncate_large_versions() -> None:
    assert naming.version_token(137) == "v137"
    assert naming.version_token(1234) == "v1234"


# --- workspace_filename ------------------------------------------------------


def test_workspace_filename_pads_version() -> None:
    assert naming.workspace_filename("A_previs", "blocking", 1) == "A_blocking_v001.mb"


def test_workspace_filename_two_and_three_digit_versions() -> None:
    assert naming.workspace_filename("A_previs", "blocking", 42) == "A_blocking_v042.mb"
    assert naming.workspace_filename("A_previs", "blocking", 137) == (
        "A_blocking_v137.mb"
    )


def test_workspace_filename_uses_bare_letter_of_split_sequence() -> None:
    assert naming.workspace_filename("G_previs_2", "acting", 7) == "G_acting_v007.mb"


def test_workspace_filename_rejects_non_sequence_code() -> None:
    with pytest.raises(ValueError):
        naming.workspace_filename("A_010", "blocking", 1)


# --- take_filename -----------------------------------------------------------


def test_take_filename_pads_version_and_uses_mov_suffix() -> None:
    assert naming.take_filename("A_010", 3) == "A_010_v003.mov"


def test_take_filename_canonicalizes_code() -> None:
    # A_10 and A_010 name the same shot, so they must name the same take file.
    assert naming.take_filename("A_10", 3) == "A_010_v003.mov"


def test_take_filename_does_not_truncate_large_versions() -> None:
    assert naming.take_filename("A_010", 1234) == "A_010_v1234.mov"


@pytest.mark.parametrize("bad", ["A_previs", "not-a-code", "A010", "", "010"])
def test_take_filename_rejects_malformed_code(bad: str) -> None:
    with pytest.raises(ValueError):
        naming.take_filename(bad, 1)
