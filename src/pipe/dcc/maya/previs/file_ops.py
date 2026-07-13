"""Previs workspace-file operations: create a new file, and branch one.

Problems an artist can fix are raised as PrevisFileError.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import maya.cmds as mc

from pipe.core.previs import load_manifest, mutate_manifest, naming
from pipe.core.util.paths import get_previs_path

from . import state

if TYPE_CHECKING:
    from pipe.core.shotgrid import SGEntity

    from .file_manager import MPrevisFileManager
    from pipe.core.previs.model import FileRecord, SequenceManifest

log = logging.getLogger(__name__)

_FIRST_VERSION = 1


class PrevisFileError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


def new_file(manager: MPrevisFileManager, entity: SGEntity, label: str) -> str:
    """Create a fresh workspace file at v001 of a new label stream; return its name."""
    sequence_code = entity.code or ""
    canonical = _normalized_label(label)

    manifest = load_manifest(sequence_code)
    if manifest.has_label(canonical):
        raise PrevisFileError(
            f"A {canonical!r} file already exists in this sequence. "
            "Open it, or branch it to keep going."
        )

    filename = naming.workspace_filename(sequence_code, canonical, _FIRST_VERSION)
    path = get_previs_path() / sequence_code / filename

    # Scene first, manifest second: if the Maya build fails, no phantom lineage
    # entry is left behind pointing at a file that was never written.
    manager._setup_file(path, entity)
    mutate_manifest(
        sequence_code,
        lambda m: m.register_file(
            filename,
            canonical,
            _FIRST_VERSION,
            None,
            created_at=state.utcnow_iso(),
        ),
    )
    log.info("Created previs workspace %s in sequence %s.", filename, sequence_code)
    return filename


def branch_current(note: str = "", *, new_label: str | None = None) -> str:
    """Checkpoint the open workspace as the next version, recording its parent.

    Saves the current file first (so the parent on disk reflects the latest work),
    then Save-As to the next version. Returns the new filename.
    """
    current = _require_open_scene()
    current_filename = current.name
    sequence_code = current.parent.name  # the sequence dir is the sequence code

    manifest = load_manifest(sequence_code)
    record = manifest.file_record(current_filename)
    label = _branch_label(new_label, record)
    version = manifest.next_version(label)

    new_filename = naming.workspace_filename(sequence_code, label, version)
    new_path = current.parent / new_filename
    # The branch is a copy of the open scene, so it starts with the same shots.
    # Membership is re-synced on the next save; seed it now so the manifest is
    # truthful the moment the branch exists.
    parent_codes = list(record.shot_codes) if record is not None else []

    _save_current_then_branch(new_path)

    def _register(m: SequenceManifest) -> None:
        m.register_file(
            new_filename,
            label,
            version,
            current_filename,
            note=note,
            created_at=state.utcnow_iso(),
        )
        m.set_membership(new_filename, parent_codes)

    mutate_manifest(sequence_code, _register)
    log.info(
        "Branched %s -> %s in sequence %s.",
        current_filename,
        new_filename,
        sequence_code,
    )
    return new_filename


def _normalized_label(label: str) -> str:
    """Canonicalize label, re-raising any error as an artist-facing PrevisFileError."""
    try:
        return naming.normalize_label(label)
    except ValueError as exc:
        raise PrevisFileError(str(exc)) from exc


def _branch_label(new_label: str | None, record: FileRecord | None) -> str:
    """The label the branch belongs to: new_label if given, else the parent's.

    With no parent record and no new_label the parent's stream is unknown, so the
    artist must name one rather than let the branch guess silently.
    """
    if new_label is not None:
        return _normalized_label(new_label)
    if record is not None:
        return record.label
    raise PrevisFileError(
        "This file is not a tracked previs workspace, so its version stream is "
        "unknown. Give the branch a label to start a new stream."
    )


def _require_open_scene() -> Path:
    scene = mc.file(query=True, sceneName=True)
    if not isinstance(scene, str) or not scene:
        raise PrevisFileError("Open and save a previs file before branching.")
    return Path(scene)


def _save_current_then_branch(new_path: Path) -> None:
    mc.file(save=True, force=True)
    mc.file(rename=str(new_path))
    mc.file(save=True, force=True)


__all__ = ["PrevisFileError", "new_file", "branch_current"]
