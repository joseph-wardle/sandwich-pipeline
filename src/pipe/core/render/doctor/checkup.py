"""One look at the farm, and what the doctor does about it.

`Doctor.check` reads every errored task of the production's jobs, or of
every job on the farm, diagnoses
each from its log, and rates every blade. It only reads, so the Sandwich
Renderer window runs it too. `Doctor.treat` carries out the plans, as only the
headless doctor does by itself.
"""

from __future__ import annotations

import logging
import socket
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pipe.core.render import farm
from pipe.core.render.doctor import attempts, blades, policy, records
from pipe.core.render.doctor.attempts import Attempt
from pipe.core.render.doctor.blades import BladeHealth, History
from pipe.core.render.doctor.diagnose import Diagnosis, diagnose
from pipe.core.render.doctor.login import Connection
from pipe.core.render.doctor.policy import Action, Plan
from pipe.core.render.doctor.records import Paths
from pipe.core.render.farm import ERROR, FarmError, Job, Task

log = logging.getLogger(__name__)

# The most tasks the doctor retries in one round, so a mistake in its rules
# can only do so much before someone sees the journal.
MAX_RETRIES_PER_ROUND = 40
# How long the doctor leaves a task alone after Tractor refused to retry it.
_REFUSED_SECONDS = 15 * 60
# How long a task sent to a machine in hyperfocus may wait to start there
# before the doctor frees it to run anywhere, as when the machine can't take it.
PIN_SECONDS = 15 * 60
# A pinned task's commands in these states are past needing their pin.
_FINISHED_STATES = {"Done", ERROR, "Skipped"}


@dataclass(frozen=True)
class Checkup:
    when: float
    # The production's unfinished jobs.
    jobs: tuple[Job, ...]
    diagnoses: tuple[Diagnosis, ...]
    blades: tuple[BladeHealth, ...]
    # What couldn't be read, worded for a person.
    problems: tuple[str, ...]
    # When each machine given a fresh start was given it.
    cleared: dict[str, float] = field(default_factory=dict)
    # Every failure the history keeps, counted or not, oldest first.
    history: tuple[blades.Failure, ...] = ()
    # How many of the examined jobs' tasks are running on each blade, as their
    # jobs say: the engine's own count per blade keeps commands it lost track
    # of, long after their jobs finished.
    rendering: dict[str, int] = field(default_factory=dict)
    # The machines in hyperfocus, with when it ends.
    focused: dict[str, float] = field(default_factory=dict)

    @property
    def suspects(self) -> set[str]:
        return {health.blade.name for health in self.blades if health.suspect}

    def plans(self, steer: bool) -> list[Plan]:
        suspects = self.suspects
        return [
            policy.plan(d, suspects, self.when, steer, self.cleared, self.focused)
            for d in self.diagnoses
        ]

    def can_take(self, machine: str, idle: bool = True) -> bool:
        """Whether `machine` is on the farm and reporting, not nimby'd, and
        with `idle`, running nothing."""
        health = next((h for h in self.blades if h.blade.name == machine), None)
        if health is None or health.blade.nimby:
            return False
        heartbeat = health.blade.heartbeat
        if heartbeat is None or self.when - heartbeat > blades.SILENT_SECONDS:
            return False
        return not (idle and self.rendering.get(machine))


@dataclass(frozen=True)
class Problem:
    """The errored tasks of one job that failed the same way, with what to do."""

    job: Job
    plans: tuple[Plan, ...]

    @property
    def first(self) -> Plan:
        return self.plans[0]


def group(plans: list[Plan]) -> list[Problem]:
    """`plans` by job and by how each task last failed, worst first: those
    that need a person, then the biggest."""
    groups: dict[tuple[int, str, Action], list[Plan]] = {}
    for plan in plans:
        d = plan.diagnosis
        groups.setdefault((d.job.jid, d.latest.signature, plan.action), []).append(plan)
    found = [Problem(group[0].diagnosis.job, tuple(group)) for group in groups.values()]
    return sorted(
        found,
        key=lambda p: (p.first.action is not Action.HOLD, -len(p.plans), -p.job.jid),
    )


@dataclass(frozen=True)
class Settings:
    # Retry for real; otherwise only note what it would do.
    acting: bool = False
    # Keep retries off blades that failed them, and give memory to those that
    # ran out (`policy.steered`).
    steer: bool = False
    # Only these artists' jobs, or everyone's when None.
    owners: frozenset[str] | None = None
    max_retries: int = MAX_RETRIES_PER_ROUND


@dataclass
class Doctor:
    """Checks the farm round after round, remembering what needn't be read again."""

    history: History
    # Each errored task's attempts, by (job, task), with the time it errored:
    # its log is only read again once it errors again.
    _logs: dict[tuple[int, int], tuple[float | None, list[Attempt]]] = field(
        default_factory=dict
    )
    # When each failed attempt was last acted on or noted, by its key.
    _handled: dict[str, float] = field(default_factory=dict)
    # How the last round logged in, or why it couldn't, for the status.
    login: str = "not needed yet"
    # Where the machines given a fresh start are noted, if anywhere.
    paths: Paths | None = None
    # Every job on the farm, not only the production's.
    all_jobs: bool = False

    def check(self, now: float | None = None) -> Checkup:
        """Read and diagnose the farm.

        Raises:
            FarmError: The engine couldn't be reached at all.
        """
        now = time.time() if now is None else now
        self.history.refresh()
        jobs = tuple(
            j
            for j in farm.all_jobs()
            if (self.all_jobs or j.sandwich) and not j.finished
        )
        diagnoses: list[Diagnosis] = []
        passed: Counter[str] = Counter()
        rendering: Counter[str] = Counter()
        problems: list[str] = []
        for job in jobs:
            if not (job.errors or job.active):
                continue
            try:
                job_tasks = farm.tasks(job)
            except FarmError as exc:
                problems.append(str(exc))
                continue
            passed.update(t.blade for t in job_tasks if t.state == "Done" and t.blade)
            rendering.update(
                t.blade for t in job_tasks if t.state == "Active" and t.blade
            )
            for task in job_tasks:
                if task.state == ERROR:
                    diagnoses.append(
                        diagnose(job, task, self._attempts(job, task, problems))
                    )
        # Forget logs of tasks that aren't errored any more.
        errored = {(d.job.jid, d.task.tid) for d in diagnoses}
        self._logs = {key: value for key, value in self._logs.items() if key in errored}
        self.history.add(f for d in diagnoses for f in blades.failures(d))
        cleared = records.cleared_since(self.paths) if self.paths else {}
        focused = (
            {m: e["until"] for m, e in records.read_focus(self.paths).items()}
            if self.paths
            else {}
        )
        health = blades.health(
            farm.blades(),
            self.history.since(now - blades.WINDOW_SECONDS),
            passed,
            now,
            cleared,
        )
        kept = sorted(
            self.history.since(now - blades.KEEP_SECONDS), key=lambda f: f.when
        )
        return Checkup(
            now,
            jobs,
            tuple(diagnoses),
            tuple(health),
            tuple(problems),
            cleared,
            tuple(kept),
            dict(rendering),
            focused,
        )

    def treat(
        self,
        checkup: Checkup,
        settings: Settings,
        connection: Callable[[], Connection] | None,
        paths: Paths,
    ) -> list[dict[str, Any]]:
        """Retry what the checkup's plans say to, and note each in the journal.

        `connection` logs in when first needed; without it, or while not
        acting, nothing is changed and the journal notes what would be.
        Returns the journal's new entries.
        """
        due = [
            p
            for p in checkup.plans(settings.steer)
            if p.action is Action.RETRY
            and (settings.owners is None or p.diagnosis.job.owner in settings.owners)
            and not self._recently_handled(p, checkup.when, settings.acting)
        ]
        due.sort(key=lambda p: p.retry_at or 0.0)
        due = due[: settings.max_retries]
        entries: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        if settings.steer:
            entries, pins = self._free_pins(checkup, settings, connection, paths)
            due = self._pin(due, checkup, settings, pins)
        entries += self.retry(due, settings, connection, paths, checkup.when)
        if settings.steer and settings.acting:
            pins += [
                {
                    "jid": e["jid"],
                    "tid": e["tid"],
                    "machine": e["pin"],
                    "when": e["when"],
                }
                for e in entries
                if e.get("pin") and e.get("result") == "retried"
            ]
            records.write_pins(paths, pins)
        return entries

    def _pin(
        self,
        due: list[Plan],
        checkup: Checkup,
        settings: Settings,
        pins: list[dict[str, Any]],
    ) -> list[Plan]:
        """`due`, with a task sent to each machine in hyperfocus that is free
        and has none waiting for it: one due now, else the soonest waiting,
        retried early to keep the machine tested."""
        waiting = sorted(
            (
                p
                for p in checkup.plans(settings.steer)
                if p.action is Action.WAIT
                and (
                    settings.owners is None or p.diagnosis.job.owner in settings.owners
                )
                and not self._recently_handled(p, checkup.when, settings.acting)
            ),
            key=lambda p: p.retry_at or 0.0,
        )
        due = list(due)
        for machine in sorted(checkup.focused):
            if any(pin["machine"] == machine for pin in pins):
                continue
            if not checkup.can_take(machine):
                continue
            taken = {(p.diagnosis.job.jid, p.diagnosis.task.tid) for p in due if p.pin}
            for plan in (*due, *waiting):
                key = (plan.diagnosis.job.jid, plan.diagnosis.task.tid)
                # One that asks for more memory might never fit; leave it be.
                if plan.pin or plan.needs_memory or key in taken:
                    continue
                pinned = replace(
                    plan,
                    pin=machine,
                    reason=f"{plan.reason.rstrip('.')}, on {machine} to test it "
                    "(hyperfocus).",
                )
                if plan in due:
                    due[due.index(plan)] = pinned
                else:
                    due.append(pinned)
                break
        return due

    def _free_pins(
        self,
        checkup: Checkup,
        settings: Settings,
        connection: Callable[[], Connection] | None,
        paths: Paths,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Free the tasks sent to machines in hyperfocus that haven't started
        and now can't, or won't soon, so they may run anywhere. Returns the
        journal's entries for those freed, and the pins still held."""
        entries, kept = [], []
        for pin in records.read_pins(paths):
            machine = str(pin.get("machine"))
            try:
                commands = farm.task_commands(int(pin["jid"]), int(pin["tid"]))
            except (FarmError, KeyError, ValueError) as exc:
                log.warning("Could not read the task pinned to %s: %s", machine, exc)
                kept.append(pin)
                continue
            mine = [c for c in commands if policy.pinned(c.service) == machine]
            if not mine or all(c.state in _FINISHED_STATES for c in mine):
                # Run, or retried since without its pin: done with.
                continue
            if any(c.state == "Active" for c in mine):
                kept.append(pin)
                continue
            stale = (
                machine not in checkup.focused
                or not checkup.can_take(machine, idle=False)
                or checkup.when - float(pin.get("when", 0)) > PIN_SECONDS
            )
            if not stale:
                kept.append(pin)
                continue
            changes = [
                (c.cid, c.service, policy.steered(c.service, (), None)) for c in mine
            ]
            entry: dict[str, Any] = {
                "when": time.time(),
                "host": socket.gethostname(),
                "acting": settings.acting,
                "jid": pin["jid"],
                "tid": pin["tid"],
                "job": "",
                "task": f"task {pin['tid']}",
                "blade": machine,
                "by": "doctor",
                "reason": f"It was sent to {machine} to test it (hyperfocus), "
                "but hasn't started there, so it may run anywhere again.",
                "services": [
                    {"cid": c, "before": b, "after": a} for c, b, a in changes
                ],
            }
            if not settings.acting or connection is None:
                entry["result"] = "would free it (watching only)"
                kept.append(pin)
            else:
                try:
                    logged_in = connection()
                    for cid, _before, after in changes:
                        logged_in.set_service(int(pin["jid"]), cid, after)
                    entry["result"] = "freed"
                except FarmError as exc:
                    entry["result"] = f"failed: {exc}"
                    kept.append(pin)
            records.write_journal(paths, entry)
            entries.append(entry)
        return entries, kept

    def retry(
        self,
        plans: list[Plan],
        settings: Settings,
        connection: Callable[[], Connection] | None,
        paths: Paths,
        now: float | None = None,
        by: str = "doctor",
    ) -> list[dict[str, Any]]:
        """Retry each plan's task, steered as its plan says, whatever its
        action, and note each in the journal as done `by` someone.

        `connection` logs in once, when first needed; without it, or while not
        acting, nothing is changed and the journal notes what would be.
        Returns the journal's new entries.
        """
        now = time.time() if now is None else now
        logged_in: Connection | None = None
        not_acting = "watching only"
        if plans and settings.acting and connection is not None:
            try:
                logged_in = connection()
                self.login = f"logged in with the {logged_in.how}"
            except FarmError as exc:
                self.login = not_acting = str(exc)
                log.warning("Could not log in to retry: %s", exc)
        entries = []
        for plan in plans:
            entry = _entry(plan, settings) | {"by": by}
            try:
                commands = farm.commands(plan.diagnosis.job, plan.diagnosis.task)
                if not any(command.state == ERROR for command in commands):
                    # Retried since it was read, as by the other of the doctor
                    # and the window: retrying it again would restart it.
                    states = sorted({command.state for command in commands})
                    entry["result"] = (
                        f"skipped: it is {', '.join(states) or 'gone'} now, not errored"
                    )
                    records.write_journal(paths, entry)
                    entries.append(entry)
                    continue
                changes = _service_changes(plan, commands)
                entry["services"] = [
                    {"cid": c, "before": b, "after": a} for c, b, a in changes
                ]
                if logged_in is not None:
                    _retry(logged_in, plan, changes)
                    entry["result"] = "retried"
                else:
                    entry["result"] = f"would retry ({not_acting})"
            except FarmError as exc:
                entry["result"] = f"failed: {exc}"
                log.warning("Could not retry %s: %s", plan.diagnosis.task.title, exc)
            self._handled[plan.diagnosis.latest.attempt.key] = now
            records.write_journal(paths, entry)
            entries.append(entry)
        return entries

    def _attempts(self, job: Job, task: Task, problems: list[str]) -> list[Attempt]:
        key = (job.jid, task.tid)
        cached = self._logs.get(key)
        if cached is not None and cached[0] == task.finished:
            return cached[1]
        try:
            found = attempts.parse(farm.log(job, task))
        except FarmError as exc:
            problems.append(str(exc))
            found = []
        self._logs[key] = (task.finished, found)
        return found

    def _recently_handled(self, plan: Plan, now: float, acting: bool) -> bool:
        """Whether its last failure was dealt with already: noted once while
        watching, or retried so recently that Tractor must have refused."""
        when = self._handled.get(plan.diagnosis.latest.attempt.key)
        if when is None:
            return False
        return not acting or now - when < _REFUSED_SECONDS


def _service_changes(
    plan: Plan, commands: list[farm.Command]
) -> list[tuple[int, str, str]]:
    """(cid, service before, service after) for each of the task's `commands`
    the retry steers."""
    # A pin from an earlier test is dropped by any retry not pinning afresh.
    if not plan.steers and not any(policy.pinned(c.service) for c in commands):
        return []
    changes = []
    for command in commands:
        memory = (
            policy.memory_needed(command.peak_memory_gb) if plan.needs_memory else None
        )
        after = policy.steered(
            command.service, plan.avoid, memory, plan.forgiven, plan.pin
        )
        if after != command.service:
            changes.append((command.cid, command.service, after))
    return changes


def _retry(
    connection: Connection, plan: Plan, changes: list[tuple[int, str, str]]
) -> None:
    job, task = plan.diagnosis.job, plan.diagnosis.task
    for cid, _before, after in changes:
        connection.set_service(job.jid, cid, after)
    connection.retry_task(job.jid, task.tid)


def _entry(plan: Plan, settings: Settings) -> dict[str, Any]:
    diagnosis = plan.diagnosis
    latest = diagnosis.latest
    return {
        "when": time.time(),
        "host": socket.gethostname(),
        "acting": settings.acting,
        "jid": diagnosis.job.jid,
        "tid": diagnosis.task.tid,
        "job": diagnosis.job.title,
        "task": diagnosis.task.title,
        "owner": diagnosis.job.owner,
        "attempts": diagnosis.attempts,
        "cause": latest.cause.label,
        "blade": latest.attempt.blade,
        "evidence": latest.evidence,
        "reason": plan.reason,
        "pin": plan.pin,
    }
