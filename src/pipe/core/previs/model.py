"""Pure data model for a previs sequence manifest.

The manifest is the durable source of truth for a previs sequence: an ordered
list of shots keyed by sticky code. Reads are forward-tolerant (unknown keys
ignored, malformed shot entries dropped); the write path in
:meth:`SequenceManifest.ensure_shot` is strict, so bad codes can never enter
a manifest through the tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from .codes import normalize_code

SCHEMA_VERSION = 1

_KEY_SCHEMA_VERSION = "schema_version"
_KEY_SEQUENCE_CODE = "sequence_code"
_KEY_SHOTS = "shots"
_KEY_CODE = "code"


@dataclass
class ManifestShot:
    """A shot's durable presence in a sequence, identified by its sticky code."""

    code: str

    def to_dict(self) -> dict[str, object]:
        return {_KEY_CODE: self.code}

    @classmethod
    def from_dict(cls, raw: object) -> ManifestShot | None:
        """Build a shot from a manifest entry, or ``None`` if it isn't one.

        Tolerant by design: anything that isn't a dict with a non-blank string
        code is treated as garbage and dropped, not raised on
        """
        if not isinstance(raw, dict):
            return None
        # json.load yields `object`; past the dict guard, JSON keys are strings.
        code = cast("dict[str, object]", raw).get(_KEY_CODE)
        if not isinstance(code, str) or not code.strip():
            return None
        return cls(code=code)


@dataclass
class SequenceManifest:
    sequence_code: str
    shots: list[ManifestShot] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def empty(cls, sequence_code: str) -> SequenceManifest:
        return cls(sequence_code=sequence_code)

    @classmethod
    def from_dict(cls, sequence_code: str, raw: object) -> SequenceManifest:
        """Parse a manifest document. ``sequence_code`` (from the on-disk path)
        is authoritative, so a copied or renamed file adopts its new location
        rather than trusting a stale stored code.
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

        version = data.get(_KEY_SCHEMA_VERSION)
        return cls(
            sequence_code=sequence_code,
            shots=shots,
            schema_version=version if isinstance(version, int) else SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            _KEY_SCHEMA_VERSION: self.schema_version,
            _KEY_SEQUENCE_CODE: self.sequence_code,
            _KEY_SHOTS: [shot.to_dict() for shot in self.shots],
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


__all__ = ["SCHEMA_VERSION", "ManifestShot", "SequenceManifest"]
