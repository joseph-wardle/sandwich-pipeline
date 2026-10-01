"""ShotGrid channel: one Note on the Shot, addressed to the downstream assignees."""

from __future__ import annotations

import logging

from pipe.core.shotgrid import (
    Shot,
    ShotGrid,
    ShotGridAmbiguous,
    ShotGridNotFound,
    User,
)

log = logging.getLogger(__name__)


def send_note(
    conn: ShotGrid,
    *,
    shot: Shot,
    steps: list[str],
    subject: str,
    body: str,
    author_name: str,
) -> dict[str, list[User]]:
    """Write the Note and return who was addressed, per Step.

    Raises:
        ShotGridError: the task lookup or the Note write failed.
    """
    tasks = conn.find_tasks(shot=shot)
    assignees = {
        step: [
            user for task in tasks if task.step == step for user in task.assignees or []
        ]
        for step in steps
    }
    to = list(
        {user.id: user for users in assignees.values() for user in users}.values()
    )
    conn.create_note(
        subject=subject,
        content=body,
        links=[shot],
        to=to,
        author=_author(conn, author_name),
    )
    return assignees


def _author(conn: ShotGrid, name: str) -> User | None:
    try:
        return conn.get_user(name=name)
    except (ShotGridNotFound, ShotGridAmbiguous) as exc:
        log.warning("Note author falls back to the script user: %s", exc)
        return None
