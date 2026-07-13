from __future__ import annotations

from typing import cast

import pytest

from pipe.core.previs.model import SCHEMA_VERSION, FileRecord, SequenceManifest


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


# --- workspace files (schema v2) ---------------------------------------------


def test_files_roundtrip() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.register_file(
        "A_blocking_v001.mb",
        "blocking",
        1,
        None,
        note="first pass",
        created_at="2026-07-13T00:00:00+00:00",
    )
    manifest.set_membership("A_blocking_v001.mb", ["A_010", "A_020"])

    restored = SequenceManifest.from_dict("A_previs", manifest.to_dict())

    record = restored.file_record("A_blocking_v001.mb")
    assert record == FileRecord(
        filename="A_blocking_v001.mb",
        label="blocking",
        version=1,
        parent_filename=None,
        note="first pass",
        created_at="2026-07-13T00:00:00+00:00",
        shot_codes=["A_010", "A_020"],
    )


def test_filename_is_the_map_key_not_stored_in_value() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.register_file("A_blocking_v001.mb", "blocking", 1, None)
    files = cast("dict[str, object]", manifest.to_dict()["files"])
    value = cast("dict[str, object]", files["A_blocking_v001.mb"])
    assert "filename" not in value


def test_from_dict_v1_document_has_empty_files() -> None:
    raw = {"schema_version": 1, "shots": [{"code": "A_010"}]}
    manifest = SequenceManifest.from_dict("A_previs", raw)
    assert manifest.files == {}
    assert manifest.codes() == ["A_010"]


def test_from_dict_filename_from_key_wins_over_stored_value() -> None:
    raw = {
        "files": {
            "A_blocking_v001.mb": {
                "filename": "stale_name.mb",
                "label": "blocking",
                "version": 1,
                "parent_filename": None,
            }
        }
    }
    record = SequenceManifest.from_dict("A_previs", raw).file_record(
        "A_blocking_v001.mb"
    )
    assert record is not None
    assert record.filename == "A_blocking_v001.mb"


def test_from_dict_drops_malformed_file_records() -> None:
    raw = {
        "files": {
            "ok.mb": {"label": "blocking", "version": 1, "parent_filename": None},
            "no_label.mb": {"version": 1},
            "blank_label.mb": {"label": "  ", "version": 1},
            "no_version.mb": {"label": "blocking"},
            "bool_version.mb": {"label": "blocking", "version": True},
            "not_a_dict.mb": "nope",
            "": {"label": "blocking", "version": 1},
        }
    }
    manifest = SequenceManifest.from_dict("A_previs", raw)
    assert list(manifest.files) == ["ok.mb"]


def test_from_dict_shot_codes_keep_only_nonblank_strings() -> None:
    raw = {
        "files": {
            "A_blocking_v001.mb": {
                "label": "blocking",
                "version": 1,
                "parent_filename": None,
                "shot_codes": ["A_010", "", 5, None, "A_020"],
            }
        }
    }
    record = SequenceManifest.from_dict("A_previs", raw).file_record(
        "A_blocking_v001.mb"
    )
    assert record is not None
    assert record.shot_codes == ["A_010", "A_020"]


def test_from_dict_blank_parent_filename_becomes_none() -> None:
    raw = {
        "files": {
            "A_blocking_v001.mb": {
                "label": "blocking",
                "version": 1,
                "parent_filename": "",
            }
        }
    }
    record = SequenceManifest.from_dict("A_previs", raw).file_record(
        "A_blocking_v001.mb"
    )
    assert record is not None
    assert record.parent_filename is None


def test_register_file_is_idempotent_and_preserves_created_at() -> None:
    manifest = SequenceManifest.empty("A_previs")
    first = manifest.register_file(
        "A_blocking_v001.mb", "blocking", 1, None, created_at="original"
    )
    manifest.set_membership("A_blocking_v001.mb", ["A_010"])

    # Re-registering the same filename must not duplicate, reset created_at,
    # or wipe membership.
    second = manifest.register_file(
        "A_blocking_v001.mb", "blocking", 1, None, created_at="later"
    )
    assert first is second
    assert second.created_at == "original"
    assert second.shot_codes == ["A_010"]
    assert list(manifest.files) == ["A_blocking_v001.mb"]


def test_register_file_records_lineage() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.register_file("A_blocking_v001.mb", "blocking", 1, None)
    child = manifest.register_file(
        "A_blocking_v002.mb", "blocking", 2, "A_blocking_v001.mb"
    )
    assert child.parent_filename == "A_blocking_v001.mb"


def test_set_membership_replaces_snapshot() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.register_file("A_blocking_v001.mb", "blocking", 1, None)
    manifest.set_membership("A_blocking_v001.mb", ["A_010", "A_020"])
    manifest.set_membership("A_blocking_v001.mb", ["A_030"])
    record = manifest.file_record("A_blocking_v001.mb")
    assert record is not None
    assert record.shot_codes == ["A_030"]


def test_set_membership_unknown_file_is_noop() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.set_membership("ghost.mb", ["A_010"])
    assert manifest.files == {}


def test_next_version_empty_stream_is_one() -> None:
    assert SequenceManifest.empty("A_previs").next_version("blocking") == 1


def test_next_version_is_per_label() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.register_file("A_blocking_v001.mb", "blocking", 1, None)
    manifest.register_file("A_blocking_v002.mb", "blocking", 2, "A_blocking_v001.mb")
    manifest.register_file("A_acting_v001.mb", "acting", 1, None)
    assert manifest.next_version("blocking") == 3
    assert manifest.next_version("acting") == 2
    assert manifest.next_version("polish") == 1


def test_has_label() -> None:
    manifest = SequenceManifest.empty("A_previs")
    manifest.register_file("A_blocking_v001.mb", "blocking", 1, None)
    assert manifest.has_label("blocking")
    assert not manifest.has_label("acting")
