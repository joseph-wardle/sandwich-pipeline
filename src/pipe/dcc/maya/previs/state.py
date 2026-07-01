"""Previs sequencer state: dataclasses + Maya `fileInfo` persistence."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import maya.cmds as mc

log = logging.getLogger(__name__)

FILEINFO_KEY = "previs_sequencer_state"
SCHEMA_VERSION = 2
DEFAULT_SHOT_DURATION = 72  # frames; 3 seconds @ 24fps


def next_shot_id() -> str:
    return f"shot_{uuid.uuid4().hex[:8]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_name(index: int) -> str:
    return f"SHOT_{(index + 1) * 10:03d}"


@dataclass
class PrevisShot:
    id: str
    primary: str = ""
    alternates: list[str] = field(default_factory=list)
    durations: dict[str, int] = field(default_factory=dict)
    shotgrid_code: str | None = None
    rlo_animation_hash: str | None = None
    cam_animation_hash: str | None = None

    @property
    def all_cameras(self) -> list[str]:
        return (
            [self.primary, *self.alternates] if self.primary else list(self.alternates)
        )

    def duration_of(self, namespace: str) -> int:
        return self.durations.get(namespace, DEFAULT_SHOT_DURATION)

    @property
    def primary_duration(self) -> int:
        return self.duration_of(self.primary)


@dataclass
class PrevisState:
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utcnow_iso)
    last_published_at: str | None = None
    notes: str = ""
    shots: list[PrevisShot] = field(default_factory=list)

    @classmethod
    def empty(cls) -> PrevisState:
        return cls()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PrevisState:
        shots_raw = raw.get("shots") or []
        shots = [PrevisShot(**_load_shot_fields(s)) for s in shots_raw]
        metadata = raw.get("metadata") or {}
        return cls(
            schema_version=int(raw.get("schema_version") or SCHEMA_VERSION),
            created_at=str(metadata.get("created_at") or utcnow_iso()),
            last_published_at=metadata.get("last_published_at"),
            notes=str(metadata.get("notes") or ""),
            shots=shots,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metadata": {
                "created_at": self.created_at,
                "last_published_at": self.last_published_at,
                "notes": self.notes,
            },
            "shots": [
                {
                    "id": s.id,
                    "primary": s.primary,
                    "alternates": list(s.alternates),
                    "durations": dict(s.durations),
                    "shotgrid_code": s.shotgrid_code,
                    "rlo_animation_hash": s.rlo_animation_hash,
                    "cam_animation_hash": s.cam_animation_hash,
                }
                for s in self.shots
            ],
        }

    def find_shot(self, shot_id: str) -> PrevisShot | None:
        return next((s for s in self.shots if s.id == shot_id), None)


def _load_shot_fields(s: dict[str, Any]) -> dict[str, Any]:
    """Build PrevisShot kwargs from raw JSON, migrating legacy v1 field names."""
    primary = str(s.get("primary") or "")
    durations_raw = s.get("durations")
    if isinstance(durations_raw, dict):
        durations = {str(k): int(v) for k, v in durations_raw.items()}
    else:
        # v1 stored a single `duration_frames` (= the primary's duration).
        legacy = int(s.get("duration_frames") or DEFAULT_SHOT_DURATION)
        durations = {primary: legacy} if primary else {}
    return dict(
        id=str(s.get("id") or next_shot_id()),
        primary=primary,
        alternates=list(s.get("alternates") or []),
        durations=durations,
        shotgrid_code=s.get("shotgrid_code"),
        rlo_animation_hash=s.get("rlo_animation_hash"),
        # v1 named the cam-bake hash `published_animation_hash`.
        cam_animation_hash=s.get("cam_animation_hash")
        or s.get("published_animation_hash"),
    )


def read_state() -> PrevisState | None:
    info = mc.fileInfo(FILEINFO_KEY, query=True)
    if not info:
        return None
    raw = info[0] if isinstance(info, (list, tuple)) else info
    if not isinstance(raw, str):
        return None
    json_text = _decode_payload(raw)
    if json_text is None:
        log.warning("Previs sequencer state in fileInfo is malformed; ignoring.")
        return None
    try:
        return PrevisState.from_dict(json.loads(json_text))
    except (json.JSONDecodeError, KeyError, ValueError):
        log.warning("Previs sequencer state in fileInfo is malformed; ignoring.")
        return None


def write_state(state: PrevisState) -> None:
    # Base64-wrap so the stored string contains only `[A-Za-z0-9+/=]`. Maya's
    # `fileInfo` round-trips raw JSON with backslash-escaped quotes (`\"`),
    # which then fails to parse. Base64 has none of the characters Maya
    # touches, so the value comes back exactly as written.
    payload = base64.b64encode(json.dumps(state.to_dict()).encode("utf-8")).decode(
        "ascii"
    )
    mc.fileInfo(FILEINFO_KEY, payload)


def _decode_payload(raw: str) -> str | None:
    """Return the JSON text from a stored `fileInfo` value.

    Handles three shapes seen in the wild:
    * **Base64** — the canonical form written by `write_state` post-fix.
    * **MEL-escaped JSON** — legacy form where Maya stored `\\"` instead of
      `"`. Recovered by unescaping the quotes.
    * **Bare JSON** — earliest legacy form (no escapes). Returned unchanged.
    """
    # Base64 first: cheap to attempt and unambiguous when it succeeds.
    try:
        decoded_bytes = base64.b64decode(raw.encode("ascii"), validate=True)
        decoded = decoded_bytes.decode("utf-8")
        if decoded.lstrip().startswith("{"):
            return decoded
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass

    stripped = raw.lstrip()
    if stripped.startswith('{\\"'):
        # MEL-escaped: every `"` was stored as `\"`. Reverse it.
        return raw.replace('\\"', '"')
    if stripped.startswith("{"):
        return raw
    return None
