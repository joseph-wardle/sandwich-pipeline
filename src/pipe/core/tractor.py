"""Reading Tractor's jobs and blades, which needs no login, and retrying and archiving jobs, which does."""

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
# Every job a pipeline tool sends sets its environment from the production's
# folders, which no other class's job names.
_SANDWICH_ENV = "/job/sandwich/"
# Each attempt at a command starts its log with a header like
# "====[2026/11/13 15:35:57 /J18142/T3/C1.1/pdolbin@host on blade ]====".
_ATTEMPT_HEADER = re.compile(r"^====\[\S+ \S+ /J\d+/T\d+/", re.MULTILINE)
_RENDER_TITLE = re.compile(r"^Render (\S+) (\d+)$")

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
    pass


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
    # Render Send's notes, such as {"shot": "A_080", "folders": {"beauty": "..."}}.
    metadata: dict[str, Any]
    # Sent by a pipeline tool, rather than by another class on the same farm.
    sandwich: bool = False

    @property
    def shot(self) -> str | None:
        shot = self.metadata.get("shot")
        return str(shot) if shot else None

    @property
    def folders(self) -> dict[str, str]:
        """The version folder each render layer writes, by layer."""
        return {str(k): str(v) for k, v in self.metadata.get("folders", {}).items()}

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
        """Where an unfinished job is in the queue, whatever its errors."""
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

    @property
    def layer_frame(self) -> tuple[str, int] | None:
        """The render layer and frame of a render task."""
        if match := _RENDER_TITLE.match(self.title):
            return match[1], int(match[2])
        return None


def render_title(layer: str, frame: int) -> str:
    return f"Render {layer} {frame}"


def render_metadata(shot: str | None, folders: dict[str, str]) -> str:
    """What Render Send notes on a job, read back as `Job.shot` and `Job.folders`."""
    notes: dict[str, Any] = {"folders": folders}
    if shot:
        notes["shot"] = shot
    return json.dumps(notes)


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
    found = [job for job in all_jobs() if job.shot == shot_code]
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
    return task_commands(job.jid, task.tid)


def task_commands(jid: int, tid: int) -> list[Command]:
    """Task `tid` of job `jid`'s commands, as `commands` gives them."""
    reply = _monitor({"q": "taskdetails", "jid": str(jid), "tid": str(tid)})
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


def is_ended(job_id: int) -> bool:
    """Whether the job is done, deleted, or gone from the tractor."""
    for jobs in _monitor({"q": "jtree", "jid": str(job_id)}).get("users", {}).values():
        data = jobs[f"J{job_id}"]["data"]
        total, _active, done, _errors = data["nTasks"]
        return data.get("deletetime") is not None or done == total
    return True


def attempts(log_text: str) -> int:
    return len(_ATTEMPT_HEADER.findall(log_text))


def retry(job: Job, task_ids: list[int], user: str, password: str) -> None:
    """Run the tasks again, or every errored task of the job when `task_ids` is empty."""

    def run(query: Any) -> None:
        if task_ids:
            for tid in task_ids:
                query.retry(f"jid={job.jid} and tid={tid}")
        else:
            query.retryerrors(f"jid={job.jid}")

    _as(user, password, f"retry job {job.jid}", run)


def archive(jobs: list[Job], user: str, password: str) -> list[int]:
    """Stop and archive the jobs, returning the ids archived."""
    # Tractor names archiving a job "delete"; "undelete" restores it.
    return _each(jobs, user, password, "archive", "archived", lambda q, s: q.delete(s))


def pause(jobs: list[Job], user: str, password: str, paused: bool = True) -> list[int]:
    """Pause the jobs, or resume them when `paused` is False, returning their ids.

    A paused job starts no new tasks; tasks already rendering finish."""
    if paused:
        return _each(jobs, user, password, "pause", "paused", lambda q, s: q.pause(s))
    return _each(jobs, user, password, "resume", "resumed", lambda q, s: q.unpause(s))


def set_priority(
    jobs: list[Job], priority: float, user: str, password: str
) -> list[int]:
    """Give the jobs `priority`, returning their ids. Higher runs sooner."""
    return _each(
        jobs,
        user,
        password,
        "change the priority of",
        "changed",
        lambda q, s: q.chpri(s, priority=priority),
    )


def _each(
    jobs: list[Job],
    user: str,
    password: str,
    verb: str,
    done_word: str,
    act: Callable[[Any, str], None],
) -> list[int]:
    """`act` on each job in turn, as `user`, returning the ids acted on. If
    Tractor stops partway, the error says which jobs were already done."""
    acted: list[int] = []

    def run(query: Any) -> None:
        for job in jobs:
            act(query, f"jid={job.jid}")
            acted.append(job.jid)

    ids = ", ".join(str(job.jid) for job in jobs)
    try:
        _as(user, password, f"{verb} job {ids}", run)
    except FarmError as exc:
        if acted and not isinstance(exc, LoginRefused):
            done = ", ".join(map(str, acted))
            these = f"Job {done} was" if len(acted) == 1 else f"Jobs {done} were"
            raise FarmError(f"{exc}. {these} {done_word} before it stopped.") from exc
        raise
    return acted


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
    # Houdini's Python looks for certificates on SideFX's build machine.
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
        # The engine's count, which keeps commands it lost track of; the
        # tasks of running jobs say what really runs (`Checkup.rendering`).
        running=int(data.get("numcmd") or 0),
        heartbeat=float(data["t"]) if data.get("t") else None,
        note=str(data.get("note") or ""),
        service_started=float(data["t0"]) if data.get("t0") else None,
    )
