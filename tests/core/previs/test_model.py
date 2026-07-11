from __future__ import annotations

import pytest

from pipe.core.previs.model import SCHEMA_VERSION, SequenceManifest


def test_to_dict_from_dict_roundtrip() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.ensure_shot("A_010")
    manifest.ensure_shot("A_020")

    restored = SequenceManifest.from_dict("A_previs", manifest.to_dict())

    assert restored.sequence_code == "A_previs"
    assert restored.codes() == ["A_010", "A_020"]
    assert restored.schema_version == SCHEMA_VERSION


def test_from_dict_preserves_shot_order() -> None:
    raw = {"shots": [{"code": "A_030"}, {"code": "A_010"}, {"code": "A_020"}]}
    assert SequenceManifest.from_dict("A_previs", raw).codes() == [
        "A_030",
        "A_010",
        "A_020",
    ]


def test_from_dict_ignores_unknown_keys() -> None:
    raw = {
        "schema_version": 1,
        "sequence_code": "ignored",
        "shots": [{"code": "A_010", "future_field": "kept-by-a-newer-tool"}],
        "unknown_top_level": {"a": 1},
    }
    manifest = SequenceManifest.from_dict("A_previs", raw)
    assert manifest.codes() == ["A_010"]


def test_from_dict_missing_shots_is_empty() -> None:
    assert SequenceManifest.from_dict("A_previs", {}).codes() == []


def test_from_dict_non_dict_is_empty() -> None:
    assert SequenceManifest.from_dict("A_previs", None).codes() == []
    assert SequenceManifest.from_dict("A_previs", []).codes() == []


def test_from_dict_preserves_higher_schema_version() -> None:
    # A newer tool's manifest must not be silently downgraded on read.
    raw = {"schema_version": 99, "shots": []}
    assert SequenceManifest.from_dict("A_previs", raw).schema_version == 99


def test_from_dict_drops_malformed_shot_entries() -> None:
    raw = {"shots": [{"code": "A_010"}, {"code": 5}, "not-a-dict", {}, {"code": ""}]}
    assert SequenceManifest.from_dict("A_previs", raw).codes() == ["A_010"]


def test_from_dict_dedups_repeated_codes() -> None:
    raw = {"shots": [{"code": "A_010"}, {"code": "A_010"}]}
    assert SequenceManifest.from_dict("A_previs", raw).codes() == ["A_010"]


def test_from_dict_sequence_code_from_path_wins() -> None:
    # The on-disk location is authoritative, so a copied file adopts its new home.
    raw = {"sequence_code": "B_previs", "shots": []}
    assert SequenceManifest.from_dict("A_previs", raw).sequence_code == "A_previs"


def test_ensure_shot_is_idempotent() -> None:
    manifest = SequenceManifest.empty("A_previs")
    first = manifest.ensure_shot("A_010")
    second = manifest.ensure_shot("A_010")
    assert first is second
    assert manifest.codes() == ["A_010"]


def test_ensure_shot_canonicalizes_before_join() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.ensure_shot("A_10")
    manifest.ensure_shot("A_010")
    assert manifest.codes() == ["A_010"]


def test_ensure_shot_appends_new_codes_in_order() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.ensure_shot("A_020")
    manifest.ensure_shot("A_010")
    assert manifest.codes() == ["A_020", "A_010"]


def test_ensure_shot_rejects_malformed_code() -> None:
    manifest = SequenceManifest.empty("A_previs")
    with pytest.raises(ValueError):
        manifest.ensure_shot("not-a-code")
    assert manifest.codes() == []
