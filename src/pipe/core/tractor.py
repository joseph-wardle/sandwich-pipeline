"""Reading Tractor's jobs, which needs no login, and retrying and archiving them, which does."""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from env import Executables

# Where every Houdini session and this window send jobs.
TRACTOR_ENGINE = "tractor-engine.cs.byu.edu:443"
TIMEOUT_SECONDS = 15.0
SYSTEM_CA_BUNDLE = "/etc/pki/tls/certs/ca-bundle.crt"
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


def log(job: Job, task: Task) -> str:
    """The command log of every attempt at `task`."""
    path = f"/tractor/cmd-logs/{job.owner}/J{job.jid}/T{task.tid}.log"
    try:
        with _open(path) as reply:
            return reply.read().decode(errors="replace")
    except OSError as exc:
        raise FarmError(
            f"Could not read the log of {task.title} ({exc}). It may not have run yet."
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


def _query(user: str, password: str) -> Any:
    # The Tractor API ships with RenderMan, outside every venv.
    api = str(Executables.rmantree / "bin")
    if api not in sys.path:
        sys.path.append(api)
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
        spooled = datetime.strptime(data.get("spooldate", ""), "%Y-%m-%d %H:%M:%S")
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
    )
