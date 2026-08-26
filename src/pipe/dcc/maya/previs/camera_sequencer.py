"""Read a legacy previs file's stock Camera Sequencer as previs state."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import cast

import maya.cmds as mc

from pipe.core.previs.codes import format_code, parse_code, shot_letter

from .state import PrevisShot, PrevisState, ShotTake, next_shot_id

log = logging.getLogger(__name__)

SHOT_NODE_TYPE = "shot"
SEQUENCER_NODE_TYPE = "sequencer"

# Legacy camera namespaces read `D_040_CAM`, often with a suffix (`_CAMRN`).
_CAMERA_CODE_RE = re.compile(r"^(?P<letter>[A-Z])_(?P<number>\d+)_CAM", re.IGNORECASE)

_REFERENCE_NODE_RE = re.compile(r"RN(\d*)$")


@dataclass(frozen=True)
class LegacyShot:
    """One `shot` node, as the Camera Sequencer stored it."""

    node: str
    name: str
    # The camera's namespace, not the camera — a take names a namespace.
    camera_namespace: str
    # Scene time the shot's animation lives at, inclusive.
    source_in: int
    source_out: int
    # Sequencer time the shot was placed at, inclusive. Discarded after
    # precedence is resolved.
    cut_in: int
    cut_out: int
    track: int
    scale: float

    @property
    def label(self) -> str:
        return self.name or self.node


@dataclass
class ImportReport:
    """Everything the import decided that an artist would not otherwise see."""

    imported: list[str] = field(default_factory=list)
    takes_added: list[tuple[str, str]] = field(default_factory=list)
    buried: list[str] = field(default_factory=list)
    no_camera: list[str] = field(default_factory=list)
    trimmed: list[tuple[str, int]] = field(default_factory=list)
    fragmented: list[str] = field(default_factory=list)
    retimed: list[tuple[str, float]] = field(default_factory=list)
    code_conflicts: list[tuple[str, str, str]] = field(default_factory=list)
    duplicate_codes: list[str] = field(default_factory=list)
    uncoded: list[str] = field(default_factory=list)
    shared_cameras: list[str] = field(default_factory=list)
    foreign_codes: list[tuple[str, str]] = field(default_factory=list)
    gaps_closed: int = 0
    other_sequencer_shots: int = 0

    def summary_lines(self) -> list[str]:
        """Artist-facing lines, one per thing the import did that changed the file."""
        lines = [
            f"{_count(self.imported, 'shot', 'shots')} imported in cut order.",
        ]
        _append_count(
            lines,
            self.takes_added,
            "camera kept as an extra take on the shot it sat under",
            "cameras kept as extra takes on the shots they sat under",
        )
        _append_count(
            lines,
            self.buried,
            "camera never played and was left out of the shot list",
            "cameras never played and were left out of the shot list",
        )
        _append_count(
            lines,
            self.no_camera,
            "shot had no camera and was skipped",
            "shots had no camera and were skipped",
        )
        if self.trimmed:
            frames = sum(count for _, count in self.trimmed)
            covered = _count(
                self.trimmed,
                "shot was covered by a higher track",
                "shots were covered by a higher track",
            )
            lines.append(f"{covered}; {frames} frames trimmed to what played.")
        _append_count(
            lines,
            self.fragmented,
            "shot played in pieces; the longest one was kept",
            "shots played in pieces; the longest piece of each was kept",
        )
        _append_count(
            lines,
            self.retimed,
            "shot was retimed in the sequencer and now plays at 1x",
            "shots were retimed in the sequencer and now play at 1x",
        )
        if self.gaps_closed:
            lines.append(
                f"{self.gaps_closed} blank frames between shots were closed up."
            )
        _append_count(
            lines, self.uncoded, "shot has no code yet", "shots have no code yet"
        )
        _append_count(
            lines,
            self.shared_cameras,
            "camera is cut into more than one shot",
            "cameras are each cut into more than one shot",
        )
        _append_count(
            lines,
            self.duplicate_codes,
            "code was used twice; the later shot was left blank",
            "codes were used twice; the later shot of each was left blank",
        )
        _append_count(
            lines,
            self.code_conflicts,
            "shot's name and camera disagreed; the name won",
            "shots' names and cameras disagreed; the names won",
        )
        _append_count(
            lines,
            self.foreign_codes,
            "shot's code belongs to another sequence and was cleared",
            "shots' codes belong to another sequence and were cleared",
        )
        if self.other_sequencer_shots:
            lines.append(
                f"{self.other_sequencer_shots} shots belong to a second sequencer. "
                "They are dropped with it, not imported."
            )
        return lines

    def detail_lines(self) -> list[str]:
        """The same decisions, named one by one, for the log.

        The dialog can only afford counts, but recovering a buried camera or a
        cleared code means knowing which one, so the names go somewhere.
        """
        named: list[tuple[str, list[str]]] = [
            ("imported", list(self.imported)),
            ("extra takes", [f"{code} += {ns}" for code, ns in self.takes_added]),
            ("buried, left out", list(self.buried)),
            ("no camera, skipped", list(self.no_camera)),
            ("trimmed", [f"{label} -{n}f" for label, n in self.trimmed]),
            ("fragmented", list(self.fragmented)),
            ("retimed", [f"{label} x{scale:g}" for label, scale in self.retimed]),
            ("uncoded", list(self.uncoded)),
            ("shared cameras", list(self.shared_cameras)),
            ("duplicate codes", list(self.duplicate_codes)),
            (
                "foreign codes cleared",
                [f"{label} ({c})" for label, c in self.foreign_codes],
            ),
            (
                "name/camera disagreed",
                [
                    f"{label}: {name} over {cam}"
                    for label, name, cam in self.code_conflicts
                ],
            ),
        ]
        return [f"{title}: {', '.join(items)}" for title, items in named if items]


def _append_count(
    lines: list[str], items: Sequence[object], singular: str, plural: str
) -> None:
    if items:
        lines.append(f"{_count(items, singular, plural)}.")


def _count(items: Sequence[object], singular: str, plural: str) -> str:
    """`3 shots were covered` — no trailing punctuation, so callers can extend it."""
    return f"{len(items)} {singular if len(items) == 1 else plural}"


# ---------- reading the scene ----------


def read_sequencer_shots() -> tuple[list[LegacyShot], int]:
    """The most-populated sequencer's shots in cut order, plus the count skipped."""
    nodes = _scene_shot_nodes()
    groups = _shots_by_sequencer(nodes)
    if not groups:
        return [], 0
    chosen = max(groups, key=lambda group: len(group))
    skipped = sum(len(g) for g in groups) - len(chosen)
    shots = sorted((_read_shot(n) for n in chosen), key=lambda s: s.cut_in)
    return shots, skipped


def import_from_scene(
    sequence_letter: str = "",
) -> tuple[PrevisState, ImportReport] | None:
    """Read the open scene's Camera Sequencer as previs state, or None if it has
    no shots of its own."""
    legacy, skipped = read_sequencer_shots()
    if not legacy:
        return None
    imported, report = build_state(legacy, sequence_letter)
    report.other_sequencer_shots = skipped
    for line in report.detail_lines():
        log.info("Camera Sequencer import — %s", line)
    return imported, report


def strip_sequencer() -> None:
    """Delete the Camera Sequencer, so the file has one shot model rather than two."""
    doomed = _scene_shot_nodes() + [
        str(n) for n in mc.ls(type=SEQUENCER_NODE_TYPE) or [] if not _is_referenced(n)
    ]
    if doomed:
        mc.delete(*doomed)


def _scene_shot_nodes() -> list[str]:
    """Shot nodes this file owns. A referenced scene can carry a cut of its own,
    which is neither ours to import nor ours to delete."""
    return [str(n) for n in mc.ls(type=SHOT_NODE_TYPE) or [] if not _is_referenced(n)]


def _is_referenced(node: str) -> bool:
    return bool(mc.referenceQuery(node, isNodeReferenced=True))


def _namespace_from_reference_name(reference_node: str) -> str:
    """Recover a namespace from a reference node an unloaded reference won't name."""
    guess = _REFERENCE_NODE_RE.sub(r"\1", reference_node)
    return guess if mc.namespace(exists=f":{guess}") else reference_node


def _shots_by_sequencer(nodes: Sequence[str]) -> list[list[str]]:
    """Partition `nodes` by owning sequencer; unowned shots form their own group."""
    groups: list[list[str]] = []
    claimed: set[str] = set()
    for sequencer in mc.ls(type=SEQUENCER_NODE_TYPE) or []:
        owned = [
            str(n) for n in mc.listConnections(f"{sequencer}.shots") or [] if n in nodes
        ]
        if owned:
            groups.append(owned)
            claimed.update(owned)
    orphans = [n for n in nodes if n not in claimed]
    if orphans:
        groups.append(orphans)
    return groups


def _read_shot(node: str) -> LegacyShot:
    return LegacyShot(
        node=node,
        name=str(mc.getAttr(f"{node}.shotName") or "").split(":")[-1],
        camera_namespace=_camera_namespace(node),
        source_in=int(mc.getAttr(f"{node}.startFrame")),
        source_out=int(mc.getAttr(f"{node}.endFrame")),
        cut_in=int(mc.getAttr(f"{node}.sequenceStartFrame")),
        cut_out=int(mc.getAttr(f"{node}.sequenceEndFrame")),
        track=int(mc.getAttr(f"{node}.track")),
        scale=float(mc.getAttr(f"{node}.scale")),
    )


def _camera_namespace(node: str) -> str:
    """The namespace of the shot's camera, or "" if it has none"""
    source = mc.connectionInfo(f"{node}.currentCamera", sourceFromDestination=True)
    if not source:
        return ""
    owner = str(source).split(".")[0]
    if mc.objExists(owner) and mc.nodeType(owner) == "reference":
        try:
            return str(cast(str, mc.referenceQuery(owner, namespace=True))).lstrip(":")
        except RuntimeError:
            return _namespace_from_reference_name(owner)
    leaf = owner.rsplit("|", 1)[-1]
    return leaf.split(":")[0] if ":" in leaf else ""


# ---------- translating to previs state ----------


@dataclass(frozen=True)
class _Played:
    """A legacy shot, the stretch of it that reached the screen, and what it became."""

    legacy: LegacyShot
    run: tuple[int, int]
    previs: PrevisShot


def build_state(
    shots: Sequence[LegacyShot], sequence_letter: str = ""
) -> tuple[PrevisState, ImportReport]:
    """Turn legacy shots into the cut they played."""
    report = ImportReport()
    usable = [s for s in shots if s.camera_namespace]
    report.no_camera = [s.label for s in shots if not s.camera_namespace]

    visible: list[tuple[LegacyShot, tuple[int, int]]] = []
    buried: list[LegacyShot] = []
    for shot in usable:
        runs = _visible_runs(shot, usable)
        if not runs:
            buried.append(shot)
            continue
        if len(runs) > 1:
            report.fragmented.append(shot.label)
        visible.append((shot, max(runs, key=_run_length)))
    visible.sort(key=lambda pair: pair[1][0])

    report.gaps_closed = _gap_frames([run for _, run in visible])
    played = [
        _Played(shot, run, _to_previs_shot(shot, run, report)) for shot, run in visible
    ]
    _assign_codes(played, sequence_letter, report)
    _place_buried(buried, played, report)

    previs_shots = [entry.previs for entry in played]
    report.imported = [s.code or s.primary for s in previs_shots]
    report.shared_cameras = _shared_cameras(previs_shots)
    return PrevisState(shots=previs_shots), report


def _to_previs_shot(
    shot: LegacyShot, run: tuple[int, int], report: ImportReport
) -> PrevisShot:
    """The shot as it played: the visible run's length, and a source_in shifted
    past whatever a higher track covered at its head."""
    duration = _run_length(run)
    lost = (shot.cut_out - shot.cut_in + 1) - duration
    if lost:
        report.trimmed.append((shot.label, lost))
    if shot.scale != 1.0:
        report.retimed.append((shot.label, shot.scale))
    return PrevisShot(
        id=next_shot_id(),
        source_in=shot.source_in + (run[0] - shot.cut_in),
        takes=[ShotTake(shot.camera_namespace, duration)],
        primary=shot.camera_namespace,
    )


def _visible_runs(
    shot: LegacyShot, others: Iterable[LegacyShot]
) -> list[tuple[int, int]]:
    """The stretches of `shot` no higher-precedence shot sits on top of."""
    covered: set[int] = set()
    for other in others:
        if _precedence(other) <= _precedence(shot):
            continue
        overlap = range(
            max(shot.cut_in, other.cut_in), min(shot.cut_out, other.cut_out) + 1
        )
        covered.update(overlap)

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for frame in range(shot.cut_in, shot.cut_out + 1):
        if frame in covered:
            if start is not None:
                runs.append((start, frame - 1))
                start = None
        elif start is None:
            start = frame
    if start is not None:
        runs.append((start, shot.cut_out))
    return runs


def _precedence(shot: LegacyShot) -> tuple[int, int, str]:
    """Maya draws the highest track; the rest of the key only breaks ties."""
    return (shot.track, shot.cut_in, shot.node)


def _run_length(run: tuple[int, int]) -> int:
    return run[1] - run[0] + 1


def _gap_frames(runs: Sequence[tuple[int, int]]) -> int:
    """Blank frames between consecutive runs — the cut closes them up."""
    return sum(
        max(0, later[0] - earlier[1] - 1) for earlier, later in zip(runs, runs[1:])
    )


def _place_buried(
    buried: Sequence[LegacyShot], played: Sequence[_Played], report: ImportReport
) -> None:
    """A camera hidden under an identical source range is a take of the shot that
    covered it; anything else is a shot that got lost and is only reported."""
    for shot in buried:
        coverer = _covering_shot(shot, played)
        if coverer is None or not _same_source_range(coverer.legacy, shot):
            report.buried.append(shot.label)
            continue
        target = coverer.previs
        # A shot node duplicated onto its own camera adds nothing the covering
        # shot's take does not already say.
        if target.find_take(shot.camera_namespace) is not None:
            continue
        target.add_take(shot.camera_namespace, target.primary_duration)
        report.takes_added.append(
            (target.code or coverer.legacy.label, shot.camera_namespace)
        )


def _same_source_range(one: LegacyShot, other: LegacyShot) -> bool:
    return one.source_in == other.source_in and one.source_out == other.source_out


def _covering_shot(shot: LegacyShot, played: Sequence[_Played]) -> _Played | None:
    """Whichever played shot is on screen where `shot` starts."""
    over = [
        entry
        for entry in played
        if entry.legacy.cut_in <= shot.cut_in <= entry.legacy.cut_out
        and _precedence(entry.legacy) > _precedence(shot)
    ]
    return max(over, key=lambda entry: _precedence(entry.legacy)) if over else None


# ---------- codes ----------


def _assign_codes(
    played: Sequence[_Played], sequence_letter: str, report: ImportReport
) -> None:
    """Code from the shot's name, else from its camera, else blank."""
    taken: set[str] = set()
    for entry in played:
        shot = entry.legacy
        from_name = _code_from(shot.label)
        from_camera = _code_from(shot.camera_namespace)
        if from_name and from_camera and from_name != from_camera:
            report.code_conflicts.append((shot.label, from_name, from_camera))
        code = from_name or from_camera
        if not code:
            report.uncoded.append(shot.label)
            continue
        if sequence_letter and shot_letter(code) != sequence_letter:
            report.foreign_codes.append((shot.label, code))
            continue
        if code in taken:
            report.duplicate_codes.append(code)
            continue
        entry.previs.code = code
        taken.add(code)


def _code_from(text: str) -> str:
    """`A_010`, or the code buried in a camera namespace (`A_010_CAMRN`); else ""."""
    parsed = parse_code(text)
    if parsed is not None:
        return format_code(*parsed)
    match = _CAMERA_CODE_RE.match(text)
    if match is None:
        return ""
    return format_code(match.group("letter").upper(), int(match.group("number")))


def _shared_cameras(previs_shots: Sequence[PrevisShot]) -> list[str]:
    """Cameras feeding more than one shot — a long move sliced into cuts."""
    counts: dict[str, int] = {}
    for shot in previs_shots:
        for namespace in shot.namespaces:
            counts[namespace] = counts.get(namespace, 0) + 1
    return sorted(ns for ns, count in counts.items() if count > 1)


__all__ = [
    "ImportReport",
    "LegacyShot",
    "build_state",
    "import_from_scene",
    "read_sequencer_shots",
    "strip_sequencer",
]
