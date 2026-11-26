"""Why a task failed, read from its log, and whether retrying it can help.

Each failed attempt is matched to a `Cause` by its exit code and its output.
A task's verdict weighs all of its attempts: most failures pass on another
try, some pass only on another blade, and some fail the same way on every
blade until a person fixes the scene, the disk or the job. Those are the ones
worth a person's time; retrying them only fills the log.

Causes are checked in `_RULES`' order, most specific first. To teach the
doctor a new failure, add a `Cause` and a `_Rule` for it, using text from a
real log.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable
from dataclasses import dataclass

from pipe.core.render.doctor.attempts import Attempt
from pipe.core.tractor import Job, Task

# The most attempts at one task before the doctor stops retrying it, whatever
# the cause. With `policy`'s waits between them, this is hours of trying.
MAX_ATTEMPTS = 20
_EVIDENCE_CHARACTERS = 300


class Remedy(enum.Enum):
    # It will likely pass on another try, as it is.
    RETRY = "retry"
    # The blade is at fault, so it should pass on another blade.
    ELSEWHERE = "elsewhere"
    # It fails the same way anywhere until a person fixes something.
    PERSON = "person"


@dataclass(frozen=True)
class Cause:
    key: str
    label: str
    remedy: Remedy
    # What it means and what a person can do, in a sentence or two.
    advice: str
    # How many different blades may fail the same way before the task itself,
    # not the blades, is judged at fault. None when it never is, as the cause
    # lies with the farm.
    blade_limit: int | None
    # Counts against the health of the blade it happened on.
    blames_blade: bool
    # Seconds to wait before retrying, at the least.
    cooldown: float = 0.0


DISK_QUOTA = Cause(
    "disk_quota",
    "Disk quota exceeded",
    Remedy.PERSON,
    "The disk quota of the folder it writes is full. Free space there, then retry.",
    blade_limit=1,
    blames_blade=False,
)
SHARED_DISK_FULL = Cause(
    "shared_disk_full",
    "Out of disk space",
    Remedy.PERSON,
    "The shared disk it writes to is full. Free space there, then retry.",
    blade_limit=1,
    blames_blade=False,
)
BLADE_DISK_FULL = Cause(
    "blade_disk_full",
    "Blade out of disk space",
    Remedy.ELSEWHERE,
    "The blade's own disk is full. It should pass on another blade.",
    blade_limit=4,
    blames_blade=True,
)
LICENSE = Cause(
    "license",
    "No license free",
    Remedy.RETRY,
    "Every license was in use for a moment. It passes once one is free.",
    blade_limit=None,
    blames_blade=False,
    cooldown=300.0,
)
COOK_ERROR = Cause(
    "cook_error",
    "Houdini couldn't cook the scene",
    Remedy.PERSON,
    "A node in the hip errors. Fix the node Houdini names, then retry or send again.",
    blade_limit=1,
    blames_blade=False,
)
OUT_OF_MEMORY = Cause(
    "out_of_memory",
    "Ran out of memory",
    Remedy.ELSEWHERE,
    "It was killed for using more memory than the blade had free. It should pass "
    "on a blade with more free memory.",
    blade_limit=None,
    blames_blade=True,
)
LOST = Cause(
    "lost",
    "Blade lost the command",
    Remedy.ELSEWHERE,
    "The blade went away before the command finished, as when it reboots or its "
    "blade service is killed for memory. It should pass on another blade.",
    blade_limit=None,
    blames_blade=True,
)
CRASH = Cause(
    "crash",
    "Crashed",
    Remedy.RETRY,
    "The program crashed. Crashes like this usually pass on another try.",
    blade_limit=None,
    blames_blade=True,
)
GPU = Cause(
    "gpu",
    "GPU error",
    Remedy.ELSEWHERE,
    "The blade's GPU failed or ran out of memory. It should pass on another blade.",
    blade_limit=4,
    blames_blade=True,
)
STORAGE = Cause(
    "storage",
    "Lost touch with the file server",
    Remedy.ELSEWHERE,
    "The blade couldn't read or write the shared disk. It should pass on another blade.",
    blade_limit=None,
    blames_blade=True,
)
COMMAND_NOT_FOUND = Cause(
    "command_not_found",
    "A program it runs is missing",
    Remedy.ELSEWHERE,
    "The blade lacks a program the command runs. If every blade lacks it, the "
    "job's environment is wrong.",
    blade_limit=3,
    blames_blade=True,
)
MISSING_FILE = Cause(
    "missing_file",
    "A file it reads is missing",
    Remedy.RETRY,
    "It couldn't find a file. If it fails on several blades, the file really is "
    "missing: publish or fix the path, then retry.",
    blade_limit=2,
    blames_blade=False,
)
PYTHON_ERROR = Cause(
    "python_error",
    "Python error",
    Remedy.RETRY,
    "A pipeline script raised an error. If it fails on several blades, the "
    "script or the scene needs a fix.",
    blade_limit=3,
    blames_blade=False,
)
STOPPED = Cause(
    "stopped",
    "Stopped by Tractor",
    Remedy.RETRY,
    "Tractor stopped it, as when someone retries, kills or ejects it, or its blade "
    "is nimbied. It passes on another try.",
    blade_limit=None,
    blames_blade=False,
)
UNKNOWN = Cause(
    "unknown",
    "Failed",
    Remedy.RETRY,
    "The doctor doesn't know this failure. Read the log.",
    blade_limit=4,
    blames_blade=False,
)


@dataclass(frozen=True)
class _Rule:
    cause: Cause
    pattern: re.Pattern[str] | None = None
    exit_codes: frozenset[int] = frozenset()


def _pattern(text: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(text, flags | re.MULTILINE)


# The blade's own disks, as opposed to the shared ones every blade writes.
_BLADE_DISKS = ("/tmp", "/var/tmp", "/usr/tmp", "/dev/shm", "/local")
_NO_SPACE = _pattern(r"No space left on device")
_RULES = (
    _Rule(DISK_QUOTA, _pattern(r"Disk quota exceeded")),
    # `_no_space` decides which disk filled; this only finds the line.
    _Rule(SHARED_DISK_FULL, _NO_SPACE),
    _Rule(
        LICENSE,
        _pattern(
            r"no (?:valid |available )?licen[cs]es?\b|licen[cs]e\b.*"
            r"(?:fail|unavailable|denied|expired|could not|unable|not available)",
            re.IGNORECASE,
        ),
        frozenset({3}),
    ),
    _Rule(COOK_ERROR, _pattern(r"Cook error|hou\.OperationFailed")),
    # commands.MEM_WATCH says this when it kills husk before the kernel does.
    _Rule(OUT_OF_MEMORY, _pattern(r"was killed with \d+ GB of memory left")),
    _Rule(
        CRASH,
        _pattern(r"Segmentation fault|Bus error|core dumped|husk crashed"),
        frozenset({134, 135, 139, -6, -7, -11}),
    ),
    _Rule(
        OUT_OF_MEMORY,
        _pattern(r"\bKilled\b|Out of memory|std::bad_alloc|MemoryError"),
        frozenset({137}),
    ),
    _Rule(
        GPU,
        _pattern(
            r"CUDA error|cudaError|OptiX error|GPU.*out of memory|out of memory.*GPU",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        STORAGE,
        _pattern(
            r"Stale file handle|Input/output error|Transport endpoint is not connected"
        ),
    ),
    _Rule(COMMAND_NOT_FOUND, _pattern(r"command not found"), frozenset({127})),
    _Rule(
        MISSING_FILE,
        _pattern(
            r"No such file or directory|Failed to open layer|Could not open asset|"
            r"Could not resolve|Unable to open file"
        ),
    ),
    _Rule(PYTHON_ERROR, _pattern(r"^Traceback \(most recent call last\)")),
    # A signal, as Tractor reports a command it killed.
    _Rule(STOPPED, exit_codes=frozenset({-2, -9, -15})),
    # Tractor's blade reports -223 for a command it lost track of when it
    # restarted (TrCmdTracker.formatExitCodeReport); the engine shows 223.
    _Rule(LOST, exit_codes=frozenset({223, -223})),
)
# Lines that are noise when looking for what went wrong: bash's trace of the
# command, and Tractor's echo of the whole command line.
_NOISE = re.compile(r"^\s*(?:\++ |\[\d{4}/\d\d/\d\d [\d:]+ exit with non-zero status)")
_HOUDINI_ERROR = _pattern(r"^\(?Error:\s+(.+?)\)?\.*$")
# husk colors its errors with HTML, which the log keeps.
_HTML_TAG = re.compile(r"</?(?:b|i|span|font)\b[^>]*>")
_PYTHON_RAISED = _pattern(
    r"^(\w+(?:\.\w+)*(?:Error|Exception|Failed|Exit|Interrupt)\b.*)$"
)


@dataclass(frozen=True)
class Finding:
    """One failed attempt and what failed it."""

    attempt: Attempt
    exit_code: int | None
    cause: Cause
    # The lines of the log that show the cause.
    evidence: str

    @property
    def signature(self) -> str:
        """The same for two attempts that failed the same way."""
        return f"{self.cause.key}:{_normalized(self.evidence)}"


class Verdict(enum.Enum):
    RETRY = "Retry"
    RETRY_ELSEWHERE = "Retry on another blade"
    PERSON = "Needs a person"


@dataclass(frozen=True)
class Diagnosis:
    job: Job
    task: Task
    # Every failed attempt, oldest first.
    findings: tuple[Finding, ...]
    # Every attempt, passed or failed.
    attempts: int
    verdict: Verdict
    # Why, in a sentence for a person.
    why: str

    @property
    def latest(self) -> Finding:
        return self.findings[-1]

    @property
    def same_way_blades(self) -> tuple[str, ...]:
        """The blades on which it failed as it last did, latest last."""
        signature = self.latest.signature
        return _distinct(
            f.attempt.blade for f in self.findings if f.signature == signature
        )

    @property
    def blamed_blades(self) -> tuple[str, ...]:
        """The blades whose fault its failures were, latest last."""
        return _distinct(f.attempt.blade for f in self.findings if f.cause.blames_blade)


def classify(attempt: Attempt, exit_code: int | None) -> Finding:
    """What failed `attempt`, which exited with `exit_code`, None when cut off."""
    if exit_code is None:
        return Finding(
            attempt, None, LOST, "The log stops before the command finished."
        )
    for rule in _RULES:
        match = rule.pattern.search(attempt.text) if rule.pattern else None
        if match is None and exit_code not in rule.exit_codes:
            continue
        cause = (
            _no_space(attempt.text) if rule.cause is SHARED_DISK_FULL else rule.cause
        )
        return Finding(
            attempt, exit_code, cause, _evidence(cause, attempt.text, match, exit_code)
        )
    return Finding(attempt, exit_code, UNKNOWN, _last_words(attempt.text, exit_code))


def diagnose(job: Job, task: Task, attempts: list[Attempt]) -> Diagnosis:
    """Why the errored `task` failed, from `attempts`, its log's attempts."""
    findings = []
    for index, attempt in enumerate(attempts):
        last = index == len(attempts) - 1
        # The engine knows the last attempt's exit when its log was cut off.
        code = (
            task.exit_code if last and attempt.exit_code is None else attempt.exit_code
        )
        if code != 0:
            findings.append(classify(attempt, code))
    if not findings:
        # No log to read, as when it was never written: judge by the exit alone.
        stand_in = Attempt(
            job.jid,
            task.tid,
            0,
            0,
            task.blade,
            task.started or 0.0,
            task.finished,
            task.exit_code,
            "",
        )
        findings.append(classify(stand_in, task.exit_code))
    verdict, why = _verdict(tuple(findings), max(len(attempts), 1))
    return Diagnosis(job, task, tuple(findings), max(len(attempts), 1), verdict, why)


def _verdict(findings: tuple[Finding, ...], attempts: int) -> tuple[Verdict, str]:
    latest = findings[-1]
    cause = latest.cause
    same_way = _distinct(
        f.attempt.blade for f in findings if f.signature == latest.signature
    )
    if cause.remedy is Remedy.PERSON:
        return Verdict.PERSON, f"{cause.label}. {cause.advice}"
    if cause.blade_limit is not None and len(same_way) >= cause.blade_limit:
        return Verdict.PERSON, (
            f"Failed the same way on {len(same_way)} different blades, so the task "
            f"itself is at fault: {cause.label.lower()}."
        )
    if attempts >= MAX_ATTEMPTS:
        return (
            Verdict.PERSON,
            f"Still failing after {attempts} attempts: {cause.label.lower()}.",
        )
    if cause.remedy is Remedy.ELSEWHERE:
        return (
            Verdict.RETRY_ELSEWHERE,
            f"{cause.label} ({latest.attempt.blade}). {cause.advice}",
        )
    return Verdict.RETRY, f"{cause.label}. {cause.advice}"


def _no_space(text: str) -> Cause:
    """Whether the disk that filled was the blade's own or a shared one."""
    for match in _NO_SPACE.finditer(text):
        full = _line_of(text, match.start())
        if any(disk in full for disk in _BLADE_DISKS):
            return BLADE_DISK_FULL
    return SHARED_DISK_FULL


def _evidence(
    cause: Cause, text: str, match: re.Match[str] | None, exit_code: int | None
) -> str:
    if cause is COOK_ERROR:
        errors = _distinct(m[1].strip() for m in _HOUDINI_ERROR.finditer(text))
        if errors:
            return _clip(" / ".join(errors[-3:]))
    if cause is PYTHON_ERROR:
        last_traceback = text.rfind("Traceback (most recent call last)")
        raised = list(_PYTHON_RAISED.finditer(text, max(last_traceback, 0)))
        if raised:
            return _clip(raised[-1][1])
    if match is not None:
        return _clip(_line_of(text, match.start()))
    if cause is LOST:
        return (
            f"Exit code {exit_code}: the blade restarted and lost track of the command."
        )
    if cause is STOPPED:
        return f"Exit code {exit_code}: Tractor stopped the command."
    return _last_words(text, exit_code)


def _last_words(text: str, exit_code: int | None) -> str:
    """The last line of the command's own output, which usually says why it stopped."""
    for line in reversed(text.splitlines()):
        if line.strip() and not _NOISE.match(line):
            return _clip(f"{line.strip()} (exit code {exit_code})")
    return f"Exit code {exit_code}, with no output to say why."


def _line_of(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start : end if end != -1 else len(text)].strip()


def _clip(text: str) -> str:
    text = _HTML_TAG.sub("", text)
    return (
        text
        if len(text) <= _EVIDENCE_CHARACTERS
        else text[: _EVIDENCE_CHARACTERS - 1] + "…"
    )


def _normalized(text: str) -> str:
    # Frame numbers, times and paths' versions differ between attempts that
    # failed the same way.
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))[:160]


def _distinct(items: Iterable[str]) -> tuple[str, ...]:
    """`items` once each, in the order each last appears."""
    found: dict[str, None] = {}
    for item in items:
        found.pop(item, None)
        found[item] = None
    return tuple(found)
