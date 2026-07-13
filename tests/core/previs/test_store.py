from __future__ import annotations

import json
import threading
from pathlib import Path

from pipe.core.previs import codes
from pipe.core.previs.model import SequenceManifest
from pipe.core.previs.store import (
    MANIFEST_FILENAME,
    load_manifest,
    manifest_path,
    mutate_manifest,
)

SEQUENCE = "A_previs"


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    manifest = load_manifest(SEQUENCE, previs_root=tmp_path)
    assert manifest.sequence_code == SEQUENCE
    assert manifest.codes() == []


def test_mutate_then_load_roundtrip(tmp_path: Path) -> None:
    mutate_manifest(SEQUENCE, lambda m: m.ensure_shot("A_010"), previs_root=tmp_path)
    mutate_manifest(SEQUENCE, lambda m: m.ensure_shot("A_020"), previs_root=tmp_path)

    reloaded = load_manifest(SEQUENCE, previs_root=tmp_path)
    assert reloaded.codes() == ["A_010", "A_020"]


def test_mutate_writes_expected_path_and_shape(tmp_path: Path) -> None:
    mutate_manifest(SEQUENCE, lambda m: m.ensure_shot("A_010"), previs_root=tmp_path)

    path = manifest_path(SEQUENCE, previs_root=tmp_path)
    assert path == tmp_path / SEQUENCE / MANIFEST_FILENAME
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {
        "schema_version": 2,
        "sequence_code": SEQUENCE,
        "shots": [{"code": "A_010"}],
        "files": {},
    }


def test_load_corrupt_returns_empty(tmp_path: Path) -> None:
    path = manifest_path(SEQUENCE, previs_root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")

    assert load_manifest(SEQUENCE, previs_root=tmp_path).codes() == []


def test_mutate_leaves_no_temp_file(tmp_path: Path) -> None:
    mutate_manifest(SEQUENCE, lambda m: m.ensure_shot("A_010"), previs_root=tmp_path)
    seq_dir = tmp_path / SEQUENCE
    assert not list(seq_dir.glob("*.tmp"))


def test_concurrent_mutate_loses_no_shot(tmp_path: Path) -> None:
    count = 20
    shot_codes = [codes.format_code("A", (i + 1) * codes.STEP) for i in range(count)]
    barrier = threading.Barrier(count)

    def add(code: str) -> None:
        barrier.wait()  # release all threads together to force contention
        mutate_manifest(SEQUENCE, lambda m: m.ensure_shot(code), previs_root=tmp_path)

    threads = [threading.Thread(target=add, args=(code,)) for code in shot_codes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    final: SequenceManifest = load_manifest(SEQUENCE, previs_root=tmp_path)
    assert sorted(final.codes()) == sorted(shot_codes)
