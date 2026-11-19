"""One look at the farm, and what the doctor does about it.

`Doctor.check` reads every errored task of the production's jobs, diagnoses
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
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class Checkup:
    when: float
    # The production's unfinished jobs.
    jobs: tuple[Job, ...]
    diagnoses: tuple[Diagnosis, ...]
    blades: tuple[BladeHealth, ...]
    # What couldn't be read, worded for a person.
    problems: tuple[str, ...]

    @property
    def suspects(self) -> set[str]:
        return {health.blade.name for health in self.blades if health.suspect}

    def plans(self, steer: bool) -> list[Plan]:
        suspects = self.suspects
        return [policy.plan(d, suspects, self.when, steer) for d in self.diagnoses]


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

    def check(self, now: float | None = None) -> Checkup:
        """Read and diagnose the farm.

        Raises:
            FarmError: The engine couldn't be reached at all.
        """
        now = time.time() if now is None else now
        self.history.refresh()
        jobs = tuple(j for j in farm.all_jobs() if j.sandwich and not j.finished)
        diagnoses: list[Diagnosis] = []
        passed: Counter[str] = Counter()
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
            for task in job_tasks:
                if task.state == ERROR:
                    diagnoses.append(
                        diagnose(job, task, self._attempts(job, task, problems))
                    )
        # Forget logs of tasks that aren't errored any more.
        errored = {(d.job.jid, d.task.tid) for d in diagnoses}
        self._logs = {key: value for key, value in self._logs.items() if key in errored}
        self.history.add(f for d in diagnoses for f in blades.failures(d))
        health = blades.health(
            farm.blades(), self.history.since(now - blades.WINDOW_SECONDS), passed, now
        )
        return Checkup(now, jobs, tuple(diagnoses), tuple(health), tuple(problems))

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
        return self.retry(
            due[: settings.max_retries], settings, connection, paths, checkup.when
        )

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
    if not plan.steers:
        return []
    changes = []
    for command in commands:
        memory = (
            policy.memory_needed(command.peak_memory_gb) if plan.needs_memory else None
        )
        after = policy.steered(command.service, plan.avoid, memory)
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
    }
