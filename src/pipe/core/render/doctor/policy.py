"""What to do about each errored task: retry it now, wait, or leave it for a person.

Tractor retries a render by itself up to three times, and only for the exit
codes in `job.RENDER_RETRIES`. The doctor keeps retrying past that, waiting
longer between attempts as they mount up, so a flaky frame gets through on
its own and a broken one isn't hammered.

A task that failed for its blade's sake is steered off that blade, and one
that ran out of memory onto a blade with more free, by narrowing the service
key of its commands (`steered`). That uses the blade's implicit service keys:
every blade offers its own hostname, so `!'yoshi'` keeps a command off yoshi,
and `@.mem` is a blade's free memory in GB.
"""

from __future__ import annotations

import enum
import math
import re
from dataclasses import dataclass

from pipe.core.render.doctor.diagnose import OUT_OF_MEMORY, Diagnosis, Verdict

# Seconds to wait after a task's last attempt before the doctor retries it,
# by how many attempts it has had. Past the end of the list, the last applies.
_WAITS = (60.0, 60.0, 60.0, 120.0, 300.0, 300.0, 600.0, 900.0, 1800.0)
# The most blades one command is kept off; the oldest give way first.
MAX_AVOIDED = 8
# A command that ran out of memory asks for a blade with this much more
# free memory than it held, within these bounds, in GB.
_MEMORY_MARGIN = 1.5
_MEMORY_FLOOR = (16, 96)
# What it asks for when Tractor didn't record how much it held.
_MEMORY_UNKNOWN = 32
# What the doctor adds to a service key: "(EL9) && !'yoshi' && @.mem > 28".
_STEERED = re.compile(r"\((?P<base>.*)\)(?P<terms>(?: && (?:!'[^']+'|@\.mem > \d+))+)")
_TERM = re.compile(r"!'(?P<blade>[^']+)'|@\.mem > (?P<memory>\d+)")
# Hostnames as they may go in a service key, so none can change its meaning.
_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Action(enum.Enum):
    RETRY = "Retry now"
    WAIT = "Retry later"
    HOLD = "Leave for a person"


@dataclass(frozen=True)
class Plan:
    diagnosis: Diagnosis
    action: Action
    # Why, in a sentence for a person.
    reason: str
    # When the task may be retried, in seconds since the epoch.
    retry_at: float | None = None
    # Blades to keep its commands off, latest last.
    avoid: tuple[str, ...] = ()
    # It ran out of memory, so wants a blade with more free.
    needs_memory: bool = False

    @property
    def steers(self) -> bool:
        return bool(self.avoid) or self.needs_memory


def plan(diagnosis: Diagnosis, suspects: set[str], now: float, steer: bool) -> Plan:
    """What to do about the errored task `diagnosis` describes.

    `suspects` are blades whose failures suggest work should stay off them.
    Without `steer`, retries go to any blade, as Tractor chooses.
    """
    job = diagnosis.job
    if diagnosis.verdict is Verdict.PERSON:
        return Plan(diagnosis, Action.HOLD, diagnosis.why)
    if job.paused:
        return Plan(diagnosis, Action.HOLD, "Its job is paused.")
    cause = diagnosis.latest.cause
    last = diagnosis.latest.attempt
    ended = last.ended or diagnosis.task.finished or last.started
    wait = max(_WAITS[min(diagnosis.attempts, len(_WAITS)) - 1], cause.cooldown)
    avoid: tuple[str, ...] = ()
    needs_memory = False
    if steer:
        avoid = tuple(
            b
            for b in (*sorted(suspects), *diagnosis.blamed_blades)
            if _HOSTNAME.match(b)
        )
        avoid = tuple(dict.fromkeys(avoid))[-MAX_AVOIDED:]
        needs_memory = cause is OUT_OF_MEMORY
    retry_at = ended + wait
    where = _where(avoid, needs_memory, steer)
    if now < retry_at:
        return Plan(
            diagnosis,
            Action.WAIT,
            f"{cause.label}; retrying {where} after a {wait / 60:.0f} minute wait.",
            retry_at,
            avoid,
            needs_memory,
        )
    return Plan(
        diagnosis,
        Action.RETRY,
        f"{cause.label}; retrying {where}.",
        retry_at,
        avoid,
        needs_memory,
    )


def memory_needed(peak_memory_gb: float) -> int:
    """The free memory, in GB, to ask for of a command that held `peak_memory_gb`."""
    if peak_memory_gb <= 0:
        return _MEMORY_UNKNOWN
    low, high = _MEMORY_FLOOR
    return min(max(math.ceil(peak_memory_gb * _MEMORY_MARGIN), low), high)


def steered(service: str, avoid: tuple[str, ...], memory_gb: int | None) -> str:
    """`service`, narrowed to keep off `avoid` and to want `memory_gb` free.

    What the doctor added before is kept, so a command steered twice is kept
    off the blades of both times.
    """
    base, avoided, memory = _unsteered(service)
    blades = tuple(dict.fromkeys((*avoided, *avoid)))[-MAX_AVOIDED:]
    if memory_gb is not None:
        memory = max(memory or 0, memory_gb)
    if not blades and memory is None:
        return service
    terms = [f"({base})"] if base else []
    terms += [f"!'{blade}'" for blade in blades]
    if memory is not None:
        terms.append(f"@.mem > {memory}")
    return " && ".join(terms)


def unsteered(service: str) -> str:
    """`service` as it was sent, without what the doctor added to it."""
    return _unsteered(service)[0]


def _unsteered(service: str) -> tuple[str, tuple[str, ...], int | None]:
    """`service` as it was sent, the blades the doctor kept it off, and the
    free memory it asked for."""
    match = _STEERED.fullmatch(service)
    if match is None:
        # Steered from no service at all, or never steered.
        if (terms := list(_TERM.finditer(service))) and all(
            part.strip() and _TERM.fullmatch(part.strip())
            for part in service.split("&&")
        ):
            return "", *_terms(terms)
        return service, (), None
    return match["base"], *_terms(list(_TERM.finditer(match["terms"])))


def _terms(terms: list[re.Match[str]]) -> tuple[tuple[str, ...], int | None]:
    blades = tuple(t["blade"] for t in terms if t["blade"])
    memories = [int(t["memory"]) for t in terms if t["memory"]]
    return blades, max(memories) if memories else None


def _where(avoid: tuple[str, ...], needs_memory: bool, steer: bool) -> str:
    if not steer:
        return "on any blade"
    parts = []
    if avoid:
        shown = ", ".join(avoid[-3:]) + (
            f" and {len(avoid) - 3} more" if len(avoid) > 3 else ""
        )
        parts.append(f"away from {shown}")
    if needs_memory:
        parts.append("on a blade with more free memory")
    return " and ".join(parts) or "on any blade"
