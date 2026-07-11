from __future__ import annotations

import json
from pathlib import Path

from pipe.core.util.atomic_json import write_json_atomic


def test_write_creates_parents_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data.json"
    payload = {"b": 2, "a": 1, "shots": ["A_010"]}

    write_json_atomic(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_write_is_sorted_and_newline_terminated(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    write_json_atomic(path, {"b": 2, "a": 1})
    text = path.read_text(encoding="utf-8")
    # sort_keys + trailing newline keep manifest diffs stable and line-based.
    assert list(json.loads(text)) == ["a", "b"]
    assert text.endswith("\n")


def test_write_overwrites_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    write_json_atomic(path, {"v": 1})
    write_json_atomic(path, {"v": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}
    assert not list(tmp_path.glob("*.tmp"))
