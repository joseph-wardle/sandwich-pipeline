"""Where each shot's Render department stands in ShotGrid: the status of the
shot's Render task, such as Hold or Approved, rather than the shot's own, and
changing it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pipe.core.shotgrid import Shot, ShotGrid, Status, Task

RENDER_STEP = "Render"
# The task to read when a shot has several Render tasks.
_MAIN_TASK = "Render Layers"


@dataclass(frozen=True)
class RenderStatus:
    task_id: int
    # The task's status code, such as "hold"; "" when it has none.
    code: str
    # What ShotGrid calls that status, with its colour and icon; None when
    # ShotGrid doesn't list it for tasks.
    status: Status | None
    # The task's name, such as "Render Layers".
    task: str

    @property
    def name(self) -> str:
        if self.status is not None:
            return self.status.name
        return self.code or "No status"


@dataclass(frozen=True)
class RenderBoard:
    # Each shot's Render task status, by shot code; shots without a Render
    # task are left out.
    shots: dict[str, RenderStatus] = field(default_factory=dict)
    # The statuses a task may be given, in ShotGrid's menu order.
    choices: list[Status] = field(default_factory=list)


def load(conn: ShotGrid) -> RenderBoard:
    choices = conn.find_statuses(entity_type="Task")
    statuses = {s.code: s for s in choices}
    by_shot: dict[str, Task] = {}
    for task in conn.find_tasks(step=RENDER_STEP):
        shot = task.as_fetched("entity")
        if not isinstance(shot, Shot) or not shot.code:
            continue
        held = by_shot.get(shot.code)
        if held is None or task.as_fetched("content") == _MAIN_TASK:
            by_shot[shot.code] = task
    found = {}
    for code, task in by_shot.items():
        # As fetched: a task without a status would fetch itself again to look.
        status_code = task.as_fetched("status") or ""
        found[code] = RenderStatus(
            task_id=task.id,
            code=status_code,
            status=statuses.get(status_code),
            task=task.as_fetched("content") or RENDER_STEP,
        )
    return RenderBoard(found, choices)


def change(conn: ShotGrid, render: RenderStatus, status: Status) -> RenderStatus:
    """Set the Render task's status in ShotGrid, and return it as it now stands.

    Raises:
        ShotGridWriteError: ShotGrid refused the change.
    """
    conn.set_task_status(Task(id=render.task_id), status.code)
    return replace(render, code=status.code, status=status)
