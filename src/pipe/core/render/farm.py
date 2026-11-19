"""What Tractor says about a shot's render jobs and the farm's blades, and
retrying and archiving jobs.

Jobs, tasks, commands, command logs and blades come from the engine's monitor
and log endpoints, which need no login, so anyone can see every artist's jobs.
Retrying and archiving go through `tractor.api.query`, which needs the Tractor
login of the job's owner or of an administrator.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from env import Executables

# Where every Houdini session and this window send jobs.
TRACTOR_ENGINE = "tractor-engine.cs.byu.edu:443"
TIMEOUT_SECONDS = 15.0
SYSTEM_CA_BUNDLE = "/etc/pki/tls/certs/ca-bundle.crt"
# Send writes this into each job's metadata; older jobs are matched by title.
SHOT_KEY = "shot"
# Every job a pipeline tool sends sets its environment from the production's
# folders, which no other class's job names.
_SANDWICH_ENV = "/job/sandwich/"
# Each attempt at a command starts its log with a header like
# "====[2026/11/13 15:35:57 /J18142/T3/C1.1/pdolbin@host on blade ]====".
_ATTEMPT_HEADER = re.compile(r"^====\[\S+ \S+ /J\d+/T\d+/", re.MULTILINE)
# Render Send names each frame's task "Render <layer> <frame>".
RENDER_TASK = re.compile(r"^Render (\S+) (\d+)$")
# A render command's stage, which sits in the version folder it renders into.
_RENDER_USD = re.compile(r"(/\S+/v\d{3,})/render\.usd\b")
# Folders found in jobs' logs, by job id; a job's folders never change.
_LOGGED_FOLDERS: dict[int, dict[str, str]] = {}

STATES = {
    "B": "Blocked",
    "R": "Ready",
    "A": "Active",
    "D": "Done",
    "E": "Error",
    "X": "Skipped",
}
ERROR = "Error"
RENDERING = "Rendering"
WAITING = "Waiting"
PAUSED = "Paused"


class FarmError(Exception):
    """Why Tractor couldn't be read or changed, worded for the artist."""


class LoginRefused(FarmError):
    """Tractor refused the user and password given for a retry or archive."""


@dataclass(frozen=True)
class Job:
    jid: int
    title: str
    owner: str
    spooled: datetime | None
    priority: float
    total: int
    active: int
    done: int
    errors: int
    paused: bool
    # Send's notes, such as {"shot": "A_080", "folders": {"beauty": "..."}}.
    metadata: dict[str, Any]
    # Sent by a pipeline tool, rather than by another class on the same farm.
    sandwich: bool = False

    @property
    def status(self) -> str:
        if self.errors:
            return ERROR
        if self.paused:
            return PAUSED
        if self.done == self.total:
            return "Done"
        if self.active:
            return RENDERING
        return WAITING

    @property
    def finished(self) -> bool:
        return self.done >= self.total

    @property
    def queue_status(self) -> str:
        """Where an unfinished job is in the queue, whatever its errors:
        RENDERING, PAUSED or WAITING."""
        if self.active:
            return RENDERING
        return PAUSED if self.paused else WAITING

    @property
    def percent(self) -> int:
        return int(100 * self.done / self.total) if self.total else 100


@dataclass(frozen=True)
class Task:
    tid: int
    title: str
    state: str
    blade: str
    exit_code: int | None
    started: float | None
    finished: float | None
    # How far along it is, 0 to 100: done is 100, and one that hasn't started
    # or that errored has none.
    percent: int | None = None
    # The ids of the commands it runs, which changing a command needs.
    cids: tuple[int, ...] = ()

    @property
    def seconds(self) -> float | None:
        if self.started is None or self.finished is None:
            return None
        return max(self.finished - self.started, 0.0)


@dataclass(frozen=True)
class Command:
    """One command of a task, as Tractor's task details describe its last attempt."""

    cid: int
    # The service key expression that chooses which blades may run it.
    service: str
    state: str
    exit_code: int | None
    # The most memory it held, in GB, as the blade reported it.
    peak_memory_gb: float
    blades: tuple[str, ...]


@dataclass(frozen=True)
class Blade:
    """A render blade, as it last reported itself to the engine."""

    name: str
    profile: str
    address: str
    # Empty when the blade takes anyone's work. Lab blades in use are set to
    # "/local/<host>", so they take only jobs sent from that computer.
    nimby: str
    # Free memory and disk, in GB, when it last reported.
    free_memory_gb: float
    free_disk_gb: float
    cpus: int
    gpu: str
    slots: int
    running: int
    # When it last reported, in seconds since the epoch.
    heartbeat: float | None
    # Tractor's own word on why it isn't taking work, such as "no free slots (1)".
    note: str
    # When its blade service last started, in seconds since the epoch: a
    # service that keeps restarting loses the commands it was running.
    service_started: float | None = None


def shot_jobs(shot_code: str) -> list[Job]:
    """Every job on the engine that renders `shot_code`, newest first."""
    pattern = _code_pattern(shot_code)
    found = [
        job
        for job in all_jobs()
        if job.metadata.get(SHOT_KEY) == shot_code or pattern.search(job.title)
    ]
    return sorted(found, key=lambda job: -job.jid)


def all_jobs() -> list[Job]:
    """Every job the engine holds, from every artist, archived ones aside."""
    found = []
    for owner, jobs in _monitor({"q": "jobs"}).get("users", {}).items():
        for raw in jobs.values():
            data = raw["data"]
            found.append(_job(owner, data, _metadata(data.get("metadata", ""))))
    return found


def queue_order(jobs: list[Job]) -> list[Job]:
    """As Tractor runs them: rendering first, then by priority, then oldest."""
    rank = {RENDERING: 0, WAITING: 1, PAUSED: 2}
    return sorted(
        jobs,
        key=lambda job: (rank[job.queue_status], -job.priority, job.jid),
    )


def shot_of(job: Job, shot_codes: list[str]) -> str | None:
    """The shot a job renders: the one Send noted, else one its title names."""
    if shot := job.metadata.get(SHOT_KEY):
        return str(shot)
    # Longest first, so "A_080_alt" is not taken for "A_080".
    for code in sorted(shot_codes, key=lambda code: len(code), reverse=True):
        if _code_pattern(code).search(job.title):
            return code
    return None


def tasks(job: Job) -> list[Task]:
    """The job's tasks that run a command, in the order the job lists them."""
    reply = _monitor({"q": "jtree", "jid": str(job.jid)})
    tree = reply.get("users", {}).get(job.owner, {}).get(f"J{job.jid}")
    if tree is None:
        raise FarmError(f"Tractor no longer has job {job.jid}.")
    found: list[Task] = []
    _collect(tree, found)
    return found


def commands(job: Job, task: Task) -> list[Command]:
    """The task's commands, with each one's service key and last attempt."""
    reply = _monitor({"q": "taskdetails", "jid": str(job.jid), "tid": str(task.tid)})
    return [_command(data) for data in reply.get("cmds") or []]


def blades() -> list[Blade]:
    """Every blade the engine knows, as each last reported."""
    return [_blade(data) for data in _monitor({"q": "blades"}).get("blades", [])]


def blade_booted(name: str) -> float | None:
    """When the blade's computer last booted, in seconds since the epoch."""
    booted = _monitor({"q": "bdetails", "b": name}).get("boottime")
    return float(booted) if booted else None


def log(job: Job, task: Task) -> str:
    """The command log of every attempt at `task`."""
    return task_log(job.owner, job.jid, task.tid, task.title)


def task_log(owner: str, jid: int, tid: int, title: str = "") -> str:
    """The command log of every attempt at task `tid` of `owner`'s job `jid`."""
    path = f"/tractor/cmd-logs/{owner}/J{jid}/T{tid}.log"
    try:
        with _open(path) as reply:
            return reply.read().decode(errors="replace")
    except OSError as exc:
        raise FarmError(
            f"Could not read the log of {title or f'task {tid}'} ({exc}). It may "
            "not have run yet."
        ) from exc


def folders(job: Job, job_tasks: list[Task]) -> dict[str, str]:
    """The version folder each of the job's render layers writes, by layer.

    Send notes them on the job. Jobs sent before it did are read from the
    log of each layer's first render task, which names the stage it renders.
    """
    if noted := job.metadata.get("folders"):
        return {str(layer): str(folder) for layer, folder in noted.items()}
    if job.jid in _LOGGED_FOLDERS:
        return _LOGGED_FOLDERS[job.jid]
    found: dict[str, str] = {}
    first: dict[str, Task] = {}
    for task in job_tasks:
        if (match := RENDER_TASK.match(task.title)) and task.state != "Blocked":
            first.setdefault(match[1], task)
    for layer, task in first.items():
        try:
            text = log(job, task)
        except FarmError:
            continue
        if match := _RENDER_USD.search(text):
            found[layer] = match[1]
    # Kept only once every layer is found, as a log may not exist yet.
    if len(found) == len(first):
        _LOGGED_FOLDERS[job.jid] = found
    return found


def attempts(log_text: str) -> int:
    return len(_ATTEMPT_HEADER.findall(log_text))


def retry(job: Job, task_ids: list[int], user: str, password: str) -> None:
    """Run the tasks again, or every errored task of the job when `task_ids` is empty.

    Raises:
        FarmError: The login was refused, or Tractor refused the retry.
    """

    def run(query: Any) -> None:
        if task_ids:
            for tid in task_ids:
                query.retry(f"jid={job.jid} and tid={tid}")
        else:
            query.retryerrors(f"jid={job.jid}")

    _as(user, password, f"retry job {job.jid}", run)


def archive(jobs: list[Job], user: str, password: str) -> list[int]:
    """Move the jobs to Tractor's archive, stopping any that still render, and
    return the ids archived. Tractor's dashboard can restore them.

    Raises:
        FarmError: The login was refused, or Tractor refused a job; the jobs
            before it in `jobs` were archived.
    """
    archived: list[int] = []

    def run(query: Any) -> None:
        for job in jobs:
            # Tractor names archiving a job "delete"; "undelete" restores it.
            query.delete(f"jid={job.jid}")
            archived.append(job.jid)

    ids = ", ".join(str(job.jid) for job in jobs)
    try:
        _as(user, password, f"archive job {ids}", run)
    except FarmError as exc:
        if archived and not isinstance(exc, LoginRefused):
            done = ", ".join(map(str, archived))
            these = f"Job {done} was" if len(archived) == 1 else f"Jobs {done} were"
            raise FarmError(f"{exc}. {these} archived before it stopped.") from exc
        raise
    return archived


def _as(user: str, password: str, action: str, run: Callable[[Any], None]) -> None:
    """Log in to Tractor as `user` and `run` with the query API."""
    query = _query(user, password)
    try:
        run(query)
    except query.PasswordRequired as exc:
        raise LoginRefused(
            f"Tractor refused the login for {user}. Check the password, and that "
            f"{user} owns the job or is a Tractor administrator."
        ) from exc
    except query.TractorQueryError as exc:
        raise FarmError(f"Tractor refused to {action}: {exc}") from exc
    finally:
        query.closeEngineClient()


def use_tractor_api() -> None:
    """Make the Tractor API importable, as `tractor.api` and `tractor.base`."""
    # The Tractor API ships with RenderMan, outside every venv.
    api = str(Executables.rmantree / "bin")
    if api not in sys.path:
        sys.path.append(api)


def _query(user: str, password: str) -> Any:
    use_tractor_api()
    import tractor.api.query as query

    host, _, port = TRACTOR_ENGINE.partition(":")
    query.setEngineClientParam(
        hostname=host, port=int(port or 80), user=user, password=password, debug=False
    )
    return query


def _monitor(params: dict[str, str]) -> dict[str, Any]:
    path = "/Tractor/monitor?" + urllib.parse.urlencode(params)
    try:
        with _open(path) as reply:
            return json.load(reply)
    except (OSError, ValueError) as exc:
        raise FarmError(
            f"Could not reach Tractor at {TRACTOR_ENGINE} ({exc}). Check your "
            "network connection, then refresh."
        ) from exc


def _open(path: str) -> Any:
    cafile = SYSTEM_CA_BUNDLE if _exists(SYSTEM_CA_BUNDLE) else None
    context = ssl.create_default_context(cafile=cafile)
    url = f"https://{TRACTOR_ENGINE}{path}"
    return urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS, context=context)


def _exists(path: str) -> bool:
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def _code_pattern(shot_code: str) -> re.Pattern[str]:
    # A_080 in "A_080 Lighting V_03", but not in "A_0805" or "XA_080".
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(shot_code)}(?![0-9])")


def _metadata(text: str) -> dict[str, Any]:
    try:
        found = json.loads(text) if text else {}
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _job(owner: str, data: dict[str, Any], metadata: dict[str, Any]) -> Job:
    total, active, done, errors = data.get("nTasks", [0, 0, 0, 0])
    try:
        # The engine gives it in UTC; shown as it is, it reads hours ahead.
        spooled = (
            datetime.strptime(data.get("spooldate", ""), "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .astimezone()
        )
    except ValueError:
        spooled = None
    return Job(
        jid=int(data["jid"]),
        title=data.get("title", ""),
        owner=owner,
        spooled=spooled,
        priority=float(data.get("priority", 0)),
        total=total,
        active=active,
        done=done,
        errors=errors,
        paused=bool(data.get("paused")),
        metadata=metadata,
        sandwich=any(_SANDWICH_ENV in str(env) for env in data.get("envkey") or []),
    )


def _collect(node: dict[str, Any], found: list[Task]) -> None:
    for child in node.get("children", []):
        data = child.get("data", {})
        if data.get("cids"):
            found.append(_task(data))
        _collect(child, found)


def _task(data: dict[str, Any]) -> Task:
    # "+A,46": the letter after the sign names the state, and the number after
    # the comma how far along it is.
    state = str(data.get("state", ""))
    code = state.lstrip("+-")[:1]
    _, _, progress = state.partition(",")
    return Task(
        tid=int(data["tid"]),
        title=data.get("title", ""),
        state=STATES.get(code, code or "?"),
        blade=data.get("blade", ""),
        exit_code=data.get("rcode"),
        started=data.get("activetime"),
        finished=data.get("statetime"),
        percent=int(progress) if progress.isdigit() else None,
        cids=tuple(int(cid) for cid in data.get("cids") or []),
    )


def _command(data: dict[str, Any]) -> Command:
    code = str(data.get("exitcode", ""))
    return Command(
        cid=int(data["cid"]),
        service=str(data.get("service") or ""),
        state=str(data.get("state") or ""),
        exit_code=int(code) if code.lstrip("-").isdigit() else None,
        peak_memory_gb=float(data.get("rss") or 0.0),
        blades=tuple(str(blade) for blade in data.get("blades") or []),
    )


def _blade(data: dict[str, Any]) -> Blade:
    return Blade(
        name=str(data.get("hnm", "")),
        profile=str(data.get("profile", "")),
        address=str(data.get("addr", "")),
        nimby=str(data.get("nimby") or ""),
        free_memory_gb=float(data.get("mem") or 0.0),
        free_disk_gb=float(data.get("disk") or 0.0),
        cpus=int(data.get("ncpu") or 0),
        gpu=str(data.get("gpulabel") or ""),
        slots=int(data.get("ns") or 0),
        running=int(data.get("numcmd") or 0),
        heartbeat=float(data["t"]) if data.get("t") else None,
        note=str(data.get("note") or ""),
        service_started=float(data["t0"]) if data.get("t0") else None,
    )
