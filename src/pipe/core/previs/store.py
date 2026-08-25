"""Disk persistence for previs sequence manifests.

Every write goes through `mutate_manifest`, which does the whole
read-modify-write under an exclusive lock. That is what keeps two
artists adding shots at once from losing each other's edits.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from ..util.atomic_json import json_write_lock, write_json_atomic
from ..util.paths import get_previs_path
from .model import SCHEMA_VERSION, SequenceManifest

log = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"
PLAYBLASTS_DIRNAME = "playblasts"


class ManifestWriteRefused(Exception):
    """The manifest on disk holds more than this version can write back."""


def manifest_path(sequence_code: str, *, previs_root: Path | None = None) -> Path:
    """Path to a sequence's manifest: `<previs_root>/<sequence_code>/manifest.json`."""
    root = previs_root if previs_root is not None else get_previs_path()
    return root / sequence_code / MANIFEST_FILENAME


def playblasts_dir(sequence_code: str, *, previs_root: Path | None = None) -> Path:
    """Directory holding a sequence's playblasts: ``<previs_root>/<seq>/playblasts``."""
    root = previs_root if previs_root is not None else get_previs_path()
    return root / sequence_code / PLAYBLASTS_DIRNAME


def load_manifest(
    sequence_code: str, *, previs_root: Path | None = None
) -> SequenceManifest:
    """Load a sequence manifest, returning an empty one if it is missing."""

    path = manifest_path(sequence_code, previs_root=previs_root)
    if not path.exists():
        return SequenceManifest.empty(sequence_code)
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        log.error(
            "Could not read previs manifest at %s (%s); treating sequence %s as empty.",
            path,
            exc,
            sequence_code,
        )
        return SequenceManifest.empty(sequence_code)
    return SequenceManifest.from_dict(sequence_code, raw)


def mutate_manifest(
    sequence_code: str,
    # Return value is discarded, so callers may return anything (e.g. the shot
    # from `ensure_shot`) without wrapping it to satisfy the type.
    mutate: Callable[[SequenceManifest], object],
    *,
    previs_root: Path | None = None,
) -> SequenceManifest:
    """Apply `mutate` to the sequence manifest and persist it atomically.

    The load, mutation, and write all happen under one lock so concurrent
    callers serialize instead of clobbering. Returns the persisted manifest.
    """
    path = manifest_path(sequence_code, previs_root=previs_root)
    with json_write_lock(path):
        manifest = load_manifest(sequence_code, previs_root=previs_root)
        _refuse_lossy_write(manifest, path)
        mutate(manifest)
        write_json_atomic(path, manifest.to_dict())
    return manifest


def _refuse_lossy_write(manifest: SequenceManifest, path: Path) -> None:
    """Stop before a rewrite that would erase what the read could not parse."""
    if manifest.schema_version > SCHEMA_VERSION:
        raise ManifestWriteRefused(
            f"{path.name} was written by a newer version of the pipeline "
            f"(format {manifest.schema_version}, this build reads {SCHEMA_VERSION}). "
            "Update your tools before editing this sequence."
        )
    if manifest.lost_entries:
        log.error(
            "Refusing to rewrite %s: %d unreadable entry/entries would be lost (%s).",
            path,
            len(manifest.lost_entries),
            ", ".join(manifest.lost_entries),
        )
        raise ManifestWriteRefused(
            f"{path.name} has {len(manifest.lost_entries)} damaged entry/entries. "
            "Saving would delete them, so nothing was written. Ask a TD to repair "
            "the manifest."
        )


__all__ = [
    "MANIFEST_FILENAME",
    "ManifestWriteRefused",
    "PLAYBLASTS_DIRNAME",
    "manifest_path",
    "playblasts_dir",
    "load_manifest",
    "mutate_manifest",
]
