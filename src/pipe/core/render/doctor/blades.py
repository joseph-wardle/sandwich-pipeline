"""Which blades keep failing work, and the failures that show it.

A task's log only holds its attempts until the task passes, and the doctor
stops reading a log once its task passes. `History` keeps the failures it has
seen, so a blade's record outlives the tasks that failed on it. The headless
doctor keeps them in a file, which the Ranch window reads too.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from pipe.core.render.doctor.diagnose import Diagnosis
from pipe.core.render.farm import Blade

# How far back a blade's failures count against it.
WINDOW_SECONDS = 12 * 3600
# Failures of work from at least this many jobs mark a blade as suspect: one
# job failing on many blades is the job's fault, not theirs.
SUSPECT_FAILURES = 6
SUSPECT_JOBS = 2
# Or this many failures of any one job's work, while its other blades pass.
SUSPECT_FAILURES_ONE_JOB = 10
# A blade that hasn't reported for this long is likely off or hung.
SILENT_SECONDS = 10 * 60
LOW_DISK_GB = 10.0
# How long `History` keeps a failure: long after it stops counting against
# its blade, so its log can still be found from the window.
KEEP_SECONDS = 31 * 24 * 3600

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Failure:
    """A failed attempt, as `History` keeps it."""

    # The attempt's key, such as "J18230/T71/C68.3".
    key: str
    jid: int
    owner: str
    task: str
    blade: str
    cause: str
    label: str
    evidence: str
    blames_blade: bool
    # When it ended, in seconds since the epoch.
    when: float


def failures(diagnosis: Diagnosis) -> list[Failure]:
    """The diagnosed task's failed attempts, as `History` keeps them."""
    job, task = diagnosis.job, diagnosis.task
    return [
        Failure(
            key=finding.attempt.key,
            jid=job.jid,
            owner=job.owner,
            task=task.title,
            blade=finding.attempt.blade,
            cause=finding.cause.key,
            label=finding.cause.label,
            evidence=finding.evidence,
            blames_blade=finding.cause.blames_blade,
            when=finding.attempt.ended or finding.attempt.started,
        )
        for finding in diagnosis.findings
        # A stand-in for a task with no log names no real attempt.
        if finding.attempt.number
    ]


class History:
    """Failed attempts seen so far, each once.

    With `path`, it reads the failures kept there, and with `write` it adds
    to them; the headless doctor writes, and the window only reads.
    """

    def __init__(self, path: Path | None = None, write: bool = False) -> None:
        self._path, self._write = path, write
        self._seen: dict[str, Failure] = {}
        # How far into the file has been read.
        self._read_to = 0
        if path is not None and write:
            self._compact(path)

    def refresh(self) -> None:
        """Read what was added to the file since it was last read."""
        if self._path is None:
            return
        try:
            with self._path.open("rb") as file:
                file.seek(self._read_to)
                lines = file.read()
        except OSError:
            return
        # Only whole lines: a writer may be partway through the last.
        whole = lines[: lines.rfind(b"\n") + 1]
        self._read_to += len(whole)
        for line in whole.splitlines():
            if failure := _parse(line):
                self._seen.setdefault(failure.key, failure)

    def add(self, found: Iterable[Failure]) -> None:
        new = [failure for failure in found if failure.key not in self._seen]
        for failure in new:
            self._seen[failure.key] = failure
        if new and self._path is not None and self._write:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as file:
                file.writelines(json.dumps(asdict(f)) + "\n" for f in new)
            # Read back by refresh() as a dupe; skip past it instead.
            self._read_to = self._path.stat().st_size

    def since(self, when: float) -> list[Failure]:
        return [f for f in self._seen.values() if f.when >= when]

    def _compact(self, path: Path) -> None:
        """Drop failures too old to matter, so the file stays small."""
        self.refresh()
        keep = time.time() - KEEP_SECONDS
        kept = [f for f in self._seen.values() if f.when >= keep]
        if len(kept) == len(self._seen):
            return
        self._seen = {f.key: f for f in kept}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            "".join(json.dumps(asdict(f)) + "\n" for f in kept), encoding="utf-8"
        )
        temporary.replace(path)
        self._read_to = path.stat().st_size


@dataclass(frozen=True)
class BladeHealth:
    blade: Blade
    # Failures within the window that were the blade's fault, latest last.
    failures: tuple[Failure, ...]
    # Tasks of the examined jobs that passed on it.
    passed: int
    # What is wrong with it, each in a few words.
    problems: tuple[str, ...]
    # Its failures suggest work should stay off it.
    suspect: bool
    # When it was given a fresh start, after which its failures count again.
    cleared: float | None = None

    @property
    def jobs(self) -> int:
        return len({f.jid for f in self.failures})


def health(
    blades: list[Blade],
    seen: list[Failure],
    passed: Counter[str],
    now: float,
    cleared: Mapping[str, float] | None = None,
) -> list[BladeHealth]:
    """Each blade's health, from its own report and the failures `seen` on it.
    `passed` counts the tasks that passed on each blade. Failures from before
    a blade's fresh start in `cleared` don't count."""
    cleared = cleared or {}
    blamed: dict[str, list[Failure]] = {}
    for failure in sorted(seen, key=lambda f: f.when):
        if (
            failure.blames_blade
            and failure.when >= now - WINDOW_SECONDS
            and failure.when >= cleared.get(failure.blade, 0.0)
        ):
            blamed.setdefault(failure.blade, []).append(failure)
    found = []
    for blade in blades:
        on_it = tuple(blamed.get(blade.name, []))
        jobs = Counter(f.jid for f in on_it)
        suspect = (len(on_it) >= SUSPECT_FAILURES and len(jobs) >= SUSPECT_JOBS) or (
            bool(jobs) and max(jobs.values()) >= SUSPECT_FAILURES_ONE_JOB
        )
        found.append(
            BladeHealth(
                blade,
                on_it,
                passed[blade.name],
                _problems(blade, on_it, now),
                suspect,
                cleared.get(blade.name),
            )
        )
    return found


def _problems(blade: Blade, on_it: tuple[Failure, ...], now: float) -> tuple[str, ...]:
    problems = []
    if blade.heartbeat is not None and now - blade.heartbeat > SILENT_SECONDS:
        problems.append(f"Not reporting for {_ago(now - blade.heartbeat)}")
    if blade.free_disk_gb < LOW_DISK_GB:
        problems.append(f"Only {blade.free_disk_gb:.0f} GB of disk free")
    if on_it:
        causes = Counter(f.label.lower() for f in on_it)
        listed = ", ".join(f"{label} ×{n}" for label, n in causes.most_common())
        jobs = len({f.jid for f in on_it})
        problems.append(
            f"{len(on_it)} failures in {jobs} job{'s' if jobs != 1 else ''}: {listed}"
        )
    return tuple(problems)


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} days"


def _parse(line: bytes) -> Failure | None:
    try:
        return Failure(**json.loads(line))
    except (ValueError, TypeError):
        log.warning(
            "Skipped an unreadable line of the doctor's history: %r", line[:200]
        )
        return None
