"""Previs sequencer state: dataclasses + persistence on a scene `network` node."""

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

# A `network` node is DG-only, so it stays out of the outliner while its string
# attribute rides the undo queue like any other attribute edit.
STATE_NODE = "previsSequencerState"
STATE_ATTR = "state"
LEGACY_FILEINFO_KEY = "previs_sequencer_state"

SCHEMA_VERSION = 4
FRAME_START = 1001
DEFAULT_SHOT_DURATION = 72  # frames; 3 seconds @ 24fps


def next_shot_id() -> str:
    return f"shot_{uuid.uuid4().hex[:8]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ShotTake:
    """One camera's rendition of a shot."""

    namespace: str
    duration: int = DEFAULT_SHOT_DURATION


@dataclass
class PrevisShot:
    id: str
    # Sticky sequence code (`A_010`) — also the ShotGrid code, when a Shot exists.
    code: str = ""
    # Scene frame the takes' keys start on. Authored, never derived.
    source_in: int = FRAME_START
    takes: list[ShotTake] = field(default_factory=list)
    # Namespace of the take that defines the shot; names an entry in `takes`
    # whenever there is one.
    primary: str = ""

    @property
    def namespaces(self) -> list[str]:
        return [t.namespace for t in self.takes]

    @property
    def primary_take(self) -> ShotTake | None:
        return self.find_take(self.primary)

    @property
    def other_takes(self) -> list[ShotTake]:
        return [t for t in self.takes if t.namespace != self.primary]

    def find_take(self, namespace: str) -> ShotTake | None:
        return next((t for t in self.takes if t.namespace == namespace), None)

    @property
    def primary_duration(self) -> int:
        take = self.primary_take
        # A shot that has lost every take still occupies a column, at default width.
        return take.duration if take else DEFAULT_SHOT_DURATION

    @property
    def source_out(self) -> int:
        """Last frame of the shot, inclusive."""
        return self.source_in + self.primary_duration - 1

    def drop_take(self, namespace: str) -> None:
        self.takes = [t for t in self.takes if t.namespace != namespace]
        self.primary = _resolve_primary(self.takes, self.primary)


@dataclass
class PrevisState:
    created_at: str = field(default_factory=utcnow_iso)
    notes: str = ""
    shots: list[PrevisShot] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PrevisState:
        shots = [_load_shot(s) for s in raw.get("shots") or []]
        if _stored_version(raw) < SCHEMA_VERSION:
            _pack_source_in(shots)
        metadata = raw.get("metadata") or {}
        return cls(
            created_at=str(metadata.get("created_at") or utcnow_iso()),
            notes=str(metadata.get("notes") or ""),
            shots=shots,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "metadata": {"created_at": self.created_at, "notes": self.notes},
            "shots": [
                {
                    "id": s.id,
                    "code": s.code,
                    "source_in": s.source_in,
                    "primary": s.primary,
                    "takes": [
                        {"namespace": t.namespace, "duration": t.duration}
                        for t in s.takes
                    ],
                }
                for s in self.shots
            ],
        }

    def find_shot(self, shot_id: str) -> PrevisShot | None:
        return next((s for s in self.shots if s.id == shot_id), None)

    def next_source_in(self) -> int:
        """First frame free of every existing shot — shots may overlap, so list
        order says nothing about which one ends latest."""
        return max((s.source_out + 1 for s in self.shots), default=FRAME_START)


def _resolve_primary(takes: list[ShotTake], primary: str) -> str:
    """The primary names one of `takes`; anything else falls back to the first."""
    if any(t.namespace == primary for t in takes):
        return primary
    return takes[0].namespace if takes else ""


# ---------- reading legacy and current payloads ----------


def _as_int(value: Any, fallback: int) -> int:
    # `bool` is an `int` subclass; a stray `true` is not a frame count.
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value


def _duration(value: Any) -> int:
    return max(1, _as_int(value, DEFAULT_SHOT_DURATION))


def _stored_version(raw: dict[str, Any]) -> int:
    # A missing version is the oldest shape; guessing high would skip the
    # migration and stack every shot on `FRAME_START`.
    return _as_int(raw.get("schema_version"), 1)


def _pack_source_in(shots: list[PrevisShot]) -> None:
    """Give pre-v4 shots the source range their stacked durations used to imply."""
    cursor = FRAME_START
    for shot in shots:
        shot.source_in = cursor
        cursor = shot.source_out + 1


def _load_shot(s: dict[str, Any]) -> PrevisShot:
    takes = _load_takes(s)
    return PrevisShot(
        id=str(s.get("id") or next_shot_id()),
        code=str(s.get("code") or ""),
        source_in=_as_int(s.get("source_in"), FRAME_START),
        takes=takes,
        primary=_resolve_primary(takes, str(s.get("primary") or "")),
    )


def _load_takes(s: dict[str, Any]) -> list[ShotTake]:
    raw = s.get("takes")
    if isinstance(raw, list):
        pairs = [
            (str(t["namespace"]), _duration(t.get("duration")))
            for t in raw
            if isinstance(t, dict) and t.get("namespace")
        ]
    else:
        # v2/v3 split the cameras across `primary` + `alternates` and kept their
        # lengths in a parallel `durations` map; v1 stored the primary's alone.
        primary = str(s.get("primary") or "")
        durations = s.get("durations")
        if not isinstance(durations, dict):
            durations = {primary: _duration(s.get("duration_frames"))}
        namespaces = ([primary] if primary else []) + [
            str(ns) for ns in (s.get("alternates") or [])
        ]
        pairs = [(ns, _duration(durations.get(ns))) for ns in namespaces if ns]

    unique: dict[str, ShotTake] = {}
    for namespace, duration in pairs:
        unique.setdefault(namespace, ShotTake(namespace, duration))
    return list(unique.values())


# ---------- persistence ----------


def read_state() -> PrevisState | None:
    """The scene's state, or None when there is none.

    The node wins outright: once it exists, a malformed payload there reads as
    nothing rather than resurrecting the legacy `fileInfo` copy.
    """
    raw = _read_node_payload() or _read_fileinfo_payload()
    if raw is None:
        return None
    try:
        # A payload that will not decode fails the same way as one that will not parse.
        return PrevisState.from_dict(json.loads(_decode_payload(raw) or ""))
    except (json.JSONDecodeError, KeyError, ValueError):
        log.warning("Previs sequencer state is malformed; ignoring.")
        return None


def write_state(state: PrevisState) -> None:
    # Base64-wrap so the stored string holds only `[A-Za-z0-9+/=]`: raw JSON came
    # back from Maya with its quotes backslash-escaped, and then failed to parse.
    payload = base64.b64encode(json.dumps(state.to_dict()).encode("utf-8")).decode(
        "ascii"
    )
    mc.setAttr(_state_plug(), payload, type="string")
    _drop_legacy_fileinfo()


def _find_state_node() -> str | None:
    nodes = mc.ls(STATE_NODE, type="network") or []
    return str(nodes[0]) if nodes else None


def _state_plug() -> str:
    node = _find_state_node()
    if node is None:
        # `skipSelect` so writing state never disturbs what the artist has selected.
        node = str(mc.createNode("network", name=STATE_NODE, skipSelect=True))
    plug = f"{node}.{STATE_ATTR}"
    if not mc.objExists(plug):
        mc.addAttr(node, longName=STATE_ATTR, dataType="string")
    return plug


def _read_node_payload() -> str | None:
    node = _find_state_node()
    if node is None:
        return None
    plug = f"{node}.{STATE_ATTR}"
    if not mc.objExists(plug):
        return None
    value = mc.getAttr(plug)
    return value if isinstance(value, str) and value else None


def _read_fileinfo_payload() -> str | None:
    info = mc.fileInfo(LEGACY_FILEINFO_KEY, query=True)
    if not info:
        return None
    raw = info[0] if isinstance(info, (list, tuple)) else info
    return raw if isinstance(raw, str) else None


def _drop_legacy_fileinfo() -> None:
    # Left behind it would outlive the node, so deleting the node would fall back
    # to a stale sequence instead of reading as empty.
    if mc.fileInfo(LEGACY_FILEINFO_KEY, query=True):
        mc.fileInfo(remove=LEGACY_FILEINFO_KEY)


def _decode_payload(raw: str) -> str | None:
    try:
        decoded = base64.b64decode(raw.encode("ascii"), validate=True).decode("utf-8")
        if decoded.lstrip().startswith("{"):
            return decoded
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    # Legacy `fileInfo` payloads held raw JSON, which Maya sometimes handed back
    # with every `"` escaped to `\"`.
    stripped = raw.lstrip()
    if stripped.startswith('{\\"'):
        return raw.replace('\\"', '"')
    return raw if stripped.startswith("{") else None
