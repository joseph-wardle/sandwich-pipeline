"""Which shots an artist lights, by their Lighting tasks in ShotGrid."""

from __future__ import annotations

import getpass
import logging

from pipe.core.shotgrid import Shot, ShotGrid, ShotGridError, User
from pipe.core.util.users import resolve_artist_display_name

log = logging.getLogger(__name__)

LIGHTING_STEP = "Lighting"

ADMINS = frozenset({"joseward", "pdolbin"})


class NotTheLighter(Exception):
    """The artist isn't assigned to light the shot, worded for them."""


def is_admin() -> bool:
    """Whether the artist may act for any shot's lighter."""
    return getpass.getuser().strip().lower() in ADMINS


def current_user(conn: ShotGrid) -> User:
    """The logged-in artist's ShotGrid user."""
    return conn.get_user(name=resolve_artist_display_name())


def lit_shots(conn: ShotGrid, user: User) -> list[Shot]:
    """The shots with a Lighting task assigned to `user`."""
    shots = {
        task.entity.id: task.entity
        for task in conn.find_tasks(user=user)
        if task.step == LIGHTING_STEP and isinstance(task.entity, Shot)
    }
    return sorted(shots.values(), key=lambda shot: shot.code or "")


def check_lighter(conn: ShotGrid, shot_code: str) -> None:
    """Raise NotTheLighter unless the artist lights `shot_code` or is an admin."""
    name = resolve_artist_display_name()
    if is_admin():
        log.info("%s publishes %s's lighting as an admin", name, shot_code)
        return
    try:
        codes = {shot.code for shot in lit_shots(conn, current_user(conn))}
    except ShotGridError as exc:
        raise NotTheLighter(
            f"ShotGrid couldn't say whether {name} lights {shot_code} ({exc}), so "
            "nothing was published. Try again."
        ) from exc
    if shot_code not in codes:
        raise NotTheLighter(
            f"ShotGrid doesn't assign {name} to the {LIGHTING_STEP} task of "
            f"{shot_code}, so only its lighter can publish its lighting. Ask "
            "production to assign you if you should."
        )
