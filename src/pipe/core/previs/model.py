"""Pure data model for a previs sequence manifest.

The manifest is the durable source of truth for a previs sequence: an ordered
list of shots keyed by sticky code (each carrying its delivered takes and a
current-take pointer), plus a map of the workspace files that make up the sequence
(each file's lineage and its snapshot of shot membership). Reads are
forward-tolerant: unknown keys are ignored and malformed entries dropped. The
write path (SequenceManifest.ensure_shot) is strict, so bad codes can never enter
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
_KEY_TAKES = "takes"
_KEY_CURRENT_VERSION = "current_version"
_KEY_SOURCE_FILENAME = "source_filename"
_KEY_CAMERA = "camera"
_KEY_DURATION_FRAMES = "duration_frames"
_KEY_FILES = "files"
_KEY_LABEL = "label"
_KEY_VERSION = "version"
_KEY_PARENT_FILENAME = "parent_filename"
_KEY_NOTE = "note"
_KEY_CREATED_AT = "created_at"
_KEY_SHOT_CODES = "shot_codes"


@dataclass
class Take:
    """One immutable per-shot previs delivery: a rendered playblast and its record.

    ``version`` is the take's identity within its shot — its own per-shot counter,
    independent of the workspace-file version. The record names the playblast on
    disk and is never mutated once written; a re-render is a new take.
    """

    version: int
    source_filename: str
    camera: str
    created_at: str
    duration_frames: int

    def to_dict(self) -> dict[str, object]:
        return {
            _KEY_VERSION: self.version,
            _KEY_SOURCE_FILENAME: self.source_filename,
            _KEY_CAMERA: self.camera,
            _KEY_CREATED_AT: self.created_at,
            _KEY_DURATION_FRAMES: self.duration_frames,
        }

    @classmethod
    def from_dict(cls, raw: object) -> Take | None:
        """Build a take from a manifest entry, or None if it is malformed.

        ``version`` is the take's identity, so an entry without an integer version
        is dropped. The remaining fields fall back to defaults; ``duration_frames``
        reaches its 0 default only under corruption, since export always records a
        real length.
        """
        if not isinstance(raw, dict):
            return None
        # json.load yields `object`; past the dict guard, JSON keys are strings.
        data = cast("dict[str, object]", raw)

        version = data.get(_KEY_VERSION)
        # `bool` is an `int` subclass; a stray `true` is not a version.
        if isinstance(version, bool) or not isinstance(version, int):
            return None

        duration = data.get(_KEY_DURATION_FRAMES)
        duration_frames = (
            duration
            if isinstance(duration, int) and not isinstance(duration, bool)
            else 0
        )
        source = data.get(_KEY_SOURCE_FILENAME)
        camera = data.get(_KEY_CAMERA)
        created_at = data.get(_KEY_CREATED_AT)
        return cls(
            version=version,
            source_filename=source if isinstance(source, str) else "",
            camera=camera if isinstance(camera, str) else "",
            created_at=created_at if isinstance(created_at, str) else "",
            duration_frames=duration_frames,
        )


@dataclass
class ManifestShot:
    """A shot's durable presence in a sequence, identified by its sticky code.

    Carries the shot's delivered takes and a ``current_version`` pointer at the take
    it currently delivers.
    """

    code: str
    takes: list[Take] = field(default_factory=list)
    current_version: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            _KEY_CODE: self.code,
            _KEY_TAKES: [take.to_dict() for take in self.takes],
            _KEY_CURRENT_VERSION: self.current_version,
        }

    @classmethod
    def from_dict(cls, raw: object) -> ManifestShot | None:
        """Build a shot from a manifest entry, or None if it isn't one.

        Tolerant by design: anything that isn't a dict with a non-blank string code
        is dropped, not treated as an error. Malformed takes are dropped and
        duplicate take versions collapse to the first seen. A current-take pointer
        naming no surviving take reads as "no current take", not a dangling
        reference.
        """
        if not isinstance(raw, dict):
            return None
        # json.load yields `object`; past the dict guard, JSON keys are strings.
        data = cast("dict[str, object]", raw)
        code = data.get(_KEY_CODE)
        if not isinstance(code, str) or not code.strip():
            return None

        takes: list[Take] = []
        seen_versions: set[int] = set()
        takes_raw = data.get(_KEY_TAKES)
        if isinstance(takes_raw, list):
            for entry in takes_raw:
                take = Take.from_dict(entry)
                if take is None or take.version in seen_versions:
                    continue
                seen_versions.add(take.version)
                takes.append(take)

        current_raw = data.get(_KEY_CURRENT_VERSION)
        # A pointer to a version with no surviving take (dropped or corrupt) is not
        # a current take. `bool` is an `int` subclass, so guard it out.
        current_version = (
            current_raw
            if isinstance(current_raw, int)
            and not isinstance(current_raw, bool)
            and current_raw in seen_versions
            else None
        )
        return cls(code=code, takes=takes, current_version=current_version)


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
        """Parse a manifest document.

        sequence_code comes from the on-disk path and is authoritative, so a
        copied or renamed file adopts its new location instead of trusting a
        stale stored code. A schema-v1 document (no files key) loads with an
        empty file map.
        """
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
        """Return the shot for ``code``, appending it if absent.

        Idempotent: assigning an existing code is a join ("this file also holds
        A_040"), not a collision. The code is canonicalized first, so ``A_10``
        and ``A_010`` resolve to the same shot.
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
        created_at. Membership is set separately by set_membership, so this
        never touches shot_codes.
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

        No-op if the file is not registered; callers register lineage first.
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

    def next_take_version(self, code: str) -> int:
        """The next free take version for a shot: highest existing + 1, else 1."""
        shot = self.find(code)
        if shot is None or not shot.takes:
            return 1
        return max(take.version for take in shot.takes) + 1

    def add_take(self, code: str, take: Take) -> ManifestShot:
        """Append a take to a shot and point its current take at it.

        Creates the shot if absent (code is canonicalized, like ensure_shot). The
        take becomes the shot's current take.
        """
        shot = self.ensure_shot(code)
        shot.takes.append(take)
        shot.current_version = take.version
        return shot

    def current_take(self, code: str) -> Take | None:
        """The take a shot currently delivers, or None if it has none.

        Resolves the current_version pointer to its take record; returns None for an
        unknown shot, an unset pointer, or (defensively) a pointer with no take.
        Callers pass a canonical code (a manifest shot's own code).
        """
        shot = self.find(code)
        if shot is None or shot.current_version is None:
            return None
        for take in shot.takes:
            if take.version == shot.current_version:
                return take
        return None


__all__ = ["SCHEMA_VERSION", "FileRecord", "ManifestShot", "SequenceManifest", "Take"]
