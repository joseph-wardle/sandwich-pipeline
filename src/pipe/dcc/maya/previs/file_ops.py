"""Previs workspace-file operations: create a file, migrate a legacy one, branch one.

Problems an artist can fix are raised as PrevisFileError.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import maya.cmds as mc

from pipe.core.previs import load_manifest, mutate_manifest, naming
from pipe.core.shotgrid import is_previs_shot_code
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


def sequence_code() -> str | None:
    """The previs sequence the open scene belongs to (`A_previs`), or None."""
    raw = mc.fileInfo("code", query=True)
    code = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
    if not isinstance(code, str) or not is_previs_shot_code(code):
        return None
    return code


def new_file(manager: MPrevisFileManager, entity: SGEntity, label: str) -> str:
    """Create a fresh workspace file at v001 of a new label stream; return its name."""
    sequence = entity.code or ""
    canonical, filename, path = _claim_first_version(
        sequence, label, "Open it, or branch it to keep going."
    )

    # Scene first, manifest second: if the Maya build fails, no phantom lineage
    # entry is left behind pointing at a file that was never written.
    manager._setup_file(path, entity)
    mutate_manifest(
        sequence,
        lambda m: m.register_file(
            filename,
            canonical,
            _FIRST_VERSION,
            None,
            created_at=state.utcnow_iso(),
        ),
    )
    log.info("Created previs workspace %s in sequence %s.", filename, sequence)
    return filename


def migrate_legacy(
    manager: MPrevisFileManager, entity: SGEntity, source: Path, label: str
) -> str:
    """Bring a pre-pipeline previs scene into `entity`'s sequence; return its name."""
    sequence = entity.code or ""
    if not source.is_file():
        raise PrevisFileError(
            f"{source.name} is not there any more. Pick another file."
        )
    canonical, filename, path = _claim_first_version(
        sequence, label, "Give the migrated file a different label."
    )

    try:
        manager._setup_migrated_file(path, entity, source)
    except RuntimeError as exc:
        # A legacy scene can refuse to open — a plugin it needs is absent, or the
        # file is damaged. Maya says so in the script editor; the artist gets a
        # sentence and their file back untouched.
        log.exception("Could not open legacy previs scene %s.", source)
        raise PrevisFileError(
            f"Maya could not open {source.name}, so nothing was migrated. "
            "Check the Script Editor for what it was missing."
        ) from exc
    mutate_manifest(
        sequence,
        lambda m: m.register_file(
            filename,
            canonical,
            _FIRST_VERSION,
            None,
            note=f"migrated from {source.name}",
            created_at=state.utcnow_iso(),
        ),
    )
    log.info("Migrated %s to %s in sequence %s.", source, filename, sequence)
    return filename


def _claim_first_version(
    sequence: str, label: str, advice: str
) -> tuple[str, str, Path]:
    """The label, filename, and path a new file's v001 takes, if they are free."""
    canonical = _normalized_label(label)
    if load_manifest(sequence).has_label(canonical):
        raise PrevisFileError(
            f"A {canonical!r} file already exists in this sequence. {advice}"
        )
    filename = naming.workspace_filename(sequence, canonical, _FIRST_VERSION)
    path = get_previs_path() / sequence / filename
    if path.exists():
        raise PrevisFileError(
            f"{filename} is already on disk in this sequence, but the manifest "
            f"does not list it. {advice}"
        )
    return canonical, filename, path


def branch_current(note: str = "", *, new_label: str | None = None) -> str:
    """Checkpoint the open workspace as the next version, recording its parent.

    Saves the current file first (so the parent on disk reflects the latest work),
    then Save-As to the next version. Returns the new filename.
    """
    current = _require_open_scene()
    current_filename = current.name
    sequence = _require_sequence_code()

    manifest = load_manifest(sequence)
    record = manifest.file_record(current_filename)
    label = _branch_label(new_label, record)
    version = manifest.next_version(label)

    new_filename = naming.workspace_filename(sequence, label, version)
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

    mutate_manifest(sequence, _register)
    log.info(
        "Branched %s -> %s in sequence %s.",
        current_filename,
        new_filename,
        sequence,
    )
    return new_filename


def _require_sequence_code() -> str:
    code = sequence_code()
    if code is None:
        raise PrevisFileError(
            "This scene is not stamped with a previs sequence, so a branch would "
            "have no sequence to belong to. Open it through Open Previs in the shelf."
        )
    return code


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


__all__ = [
    "PrevisFileError",
    "branch_current",
    "migrate_legacy",
    "new_file",
    "sequence_code",
]
