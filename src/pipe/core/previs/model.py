"""Pure data model for a previs sequence manifest.

The manifest is an ordered list of shots keyed by code, plus a map of the workspace
files that make up the sequence. Unknown keys are ignored and malformed entries
dropped. The write path (SequenceManifest.ensure_shot) is strict, so bad codes can never enter
a manifest through the tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from .codes import normalize_code

SCHEMA_VERSION = 3

_KEY_SCHEMA_VERSION = "schema_version"
_KEY_SEQUENCE_CODE = "sequence_code"
_KEY_SHOTS = "shots"
_KEY_CODE = "code"
_KEY_FILES = "files"
_KEY_LABEL = "label"
_KEY_VERSION = "version"
_KEY_PARENT_FILENAME = "parent_filename"
_KEY_NOTE = "note"
_KEY_CREATED_AT = "created_at"
_KEY_SHOT_CODES = "shot_codes"


@dataclass
class ManifestShot:
    """A shot's durable presence in a sequence, identified by its sticky code."""

    code: str

    def to_dict(self) -> dict[str, object]:
        return {_KEY_CODE: self.code}

    @classmethod
    def from_dict(cls, raw: object) -> ManifestShot | None:
        """Build a shot from a manifest entry, or None if it isn't one.

        Anything that isn't a dict with a non-blank string code is dropped,
        not treated as an error.
        """
        if not isinstance(raw, dict):
            return None
        # json.load yields `object`; past the dict guard, JSON keys are strings.
        data = cast("dict[str, object]", raw)
        code = data.get(_KEY_CODE)
        if not isinstance(code, str) or not code.strip():
            return None
        return cls(code=code)


@dataclass
class FileRecord:
    """One workspace file's lineage and its snapshot of shot membership.

    The filename is the record's identity and its map key. label and version are
    denormalized from the filename.
    """

    filename: str
    label: str
    version: int
    parent_filename: str | None  # lineage edge; None = fresh start (a "new file")
    note: str = ""
    created_at: str = ""
    shot_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        # `filename` is the map key, so it is not repeated in the value.
        return {
            _KEY_LABEL: self.label,
            _KEY_VERSION: self.version,
            _KEY_PARENT_FILENAME: self.parent_filename,
            _KEY_NOTE: self.note,
            _KEY_CREATED_AT: self.created_at,
            _KEY_SHOT_CODES: list(self.shot_codes),
        }

    @classmethod
    def from_dict(cls, filename: str, raw: object) -> FileRecord | None:
        """Build a file record from a manifest entry, or None if it is malformed.

        filename comes from the map key (the on-disk identity), so any filename
        stored inside the value is ignored. A record missing a label or an integer
        version is dropped. The remaining fields fall back to defaults.
        """
        if not isinstance(raw, dict):
            return None
        # json.load yields `object`; past the dict guard, JSON keys are strings.
        data = cast("dict[str, object]", raw)

        label = data.get(_KEY_LABEL)
        if not isinstance(label, str) or not label.strip():
            return None
        version = data.get(_KEY_VERSION)
        # `bool` is an `int` subclass; a stray `true` is not a version.
        if isinstance(version, bool) or not isinstance(version, int):
            return None

        parent = data.get(_KEY_PARENT_FILENAME)
        parent_filename = parent if isinstance(parent, str) and parent.strip() else None
        note = data.get(_KEY_NOTE)
        created_at = data.get(_KEY_CREATED_AT)
        codes_raw = data.get(_KEY_SHOT_CODES)
        shot_codes = (
            [c for c in codes_raw if isinstance(c, str) and c.strip()]
            if isinstance(codes_raw, list)
            else []
        )
        return cls(
            filename=filename,
            label=label,
            version=version,
            parent_filename=parent_filename,
            note=note if isinstance(note, str) else "",
            created_at=created_at if isinstance(created_at, str) else "",
            shot_codes=shot_codes,
        )


@dataclass
class SequenceManifest:
    sequence_code: str
    shots: list[ManifestShot] = field(default_factory=list)
    files: dict[str, FileRecord] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def empty(cls, sequence_code: str) -> SequenceManifest:
        return cls(sequence_code=sequence_code)

    @classmethod
    def from_dict(cls, sequence_code: str, raw: object) -> SequenceManifest:
        """Parse a manifest document."""

        if not isinstance(raw, dict):
            return cls.empty(sequence_code)
        # json.load yields `object`; past the dict guard, JSON keys are strings.
        data = cast("dict[str, object]", raw)

        shots: list[ManifestShot] = []
        seen: set[str] = set()
        shots_raw = data.get(_KEY_SHOTS)
        if isinstance(shots_raw, list):
            for entry in shots_raw:
                shot = ManifestShot.from_dict(entry)
                if shot is None or shot.code in seen:
                    continue
                seen.add(shot.code)
                shots.append(shot)

        files: dict[str, FileRecord] = {}
        files_raw = data.get(_KEY_FILES)
        if isinstance(files_raw, dict):
            for filename, entry in cast("dict[str, object]", files_raw).items():
                if not isinstance(filename, str) or not filename.strip():
                    continue
                record = FileRecord.from_dict(filename, entry)
                if record is not None:
                    files[filename] = record

        version = data.get(_KEY_SCHEMA_VERSION)
        return cls(
            sequence_code=sequence_code,
            shots=shots,
            files=files,
            schema_version=version if isinstance(version, int) else SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            _KEY_SCHEMA_VERSION: self.schema_version,
            _KEY_SEQUENCE_CODE: self.sequence_code,
            _KEY_SHOTS: [shot.to_dict() for shot in self.shots],
            _KEY_FILES: {name: record.to_dict() for name, record in self.files.items()},
        }

    def find(self, code: str) -> ManifestShot | None:
        for shot in self.shots:
            if shot.code == code:
                return shot
        return None

    def codes(self) -> list[str]:
        return [shot.code for shot in self.shots]

    def ensure_shot(self, code: str) -> ManifestShot:
        """Return the shot for `code`, appending it if absent.

        Assigning an existing code is a join ("this file also holds A_040"),
        not a collision. The code is canonicalized first, so `A_10` and
        `A_010` resolve to the same shot.
        """
        canonical = normalize_code(code)
        existing = self.find(canonical)
        if existing is not None:
            return existing
        shot = ManifestShot(code=canonical)
        self.shots.append(shot)
        return shot

    def file_record(self, filename: str) -> FileRecord | None:
        return self.files.get(filename)

    def register_file(
        self,
        filename: str,
        label: str,
        version: int,
        parent_filename: str | None,
        *,
        note: str = "",
        created_at: str = "",
    ) -> FileRecord:
        """Record a workspace file's lineage; return the existing record if present.

        A file's label, version, and parent are fixed by its filename, so
        re-registering the same filename is a no-op that keeps the original
        created_at.
        """
        existing = self.files.get(filename)
        if existing is not None:
            return existing
        record = FileRecord(
            filename=filename,
            label=label,
            version=version,
            parent_filename=parent_filename,
            note=note,
            created_at=created_at,
        )
        self.files[filename] = record
        return record

    def set_membership(self, filename: str, codes: list[str]) -> None:
        """Replace a file's shot-membership snapshot.

        No-op if the file is not registered.
        """
        record = self.files.get(filename)
        if record is None:
            return
        record.shot_codes = list(codes)

    def next_version(self, label: str) -> int:
        """The next free version in label's stream: highest seen + 1, else 1.

        Counted from the records in the manifest, not by scanning filenames on disk.
        """
        versions = [f.version for f in self.files.values() if f.label == label]
        return (max(versions) + 1) if versions else 1

    def has_label(self, label: str) -> bool:
        return any(f.label == label for f in self.files.values())


__all__ = [
    "SCHEMA_VERSION",
    "FileRecord",
    "ManifestShot",
    "SequenceManifest",
]
