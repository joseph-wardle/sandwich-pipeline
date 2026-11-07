"""ShotGrid channel: who is downstream, and one Note on the deliverable to them."""

from __future__ import annotations

import logging

from pipe.core.shotgrid import Asset, Shot, ShotGrid, ShotGridNotFound, User
from pipe.core.util.users import current_shotgrid_id

log = logging.getLogger(__name__)

# A Task status: the work isn't happening on this deliverable, so nobody is told.
OMITTED = "omt"


def find_assignees(
    conn: ShotGrid, deliverable: Shot | Asset, steps: list[str]
) -> dict[str, list[User]]:
    """Who is assigned to each Step's Tasks on `deliverable`, once each.

    Raises:
        ShotGridError: the task lookup failed.
    """
    tasks = conn.find_tasks(
        shot=deliverable if isinstance(deliverable, Shot) else None,
        asset=deliverable if isinstance(deliverable, Asset) else None,
    )
    return {
        step: list(
            {
                user.id: user
                for task in tasks
                if task.step == step and task.status != OMITTED
                for user in task.assignees or []
            }.values()
        )
        for step in steps
    }


def send_note(
    conn: ShotGrid,
    *,
    deliverable: Shot | Asset,
    to: list[User],
    subject: str,
    body: str,
) -> None:
    """Write the Note, from the person publishing.

    Raises:
        ShotGridError: the Note write failed.
    """
    conn.create_note(
        subject=subject,
        content=body,
        links=[deliverable],
        to=to,
        author=_author(conn),
    )


def _author(conn: ShotGrid) -> User | None:
    user_id = current_shotgrid_id()
    if user_id is None:
        log.warning(
            "Note author falls back to the script user: this login has no "
            "ShotGrid id in people.json"
        )
        return None
    try:
        return conn.get_user(id=user_id)
    except ShotGridNotFound as exc:
        log.warning("Note author falls back to the script user: %s", exc)
        return None
