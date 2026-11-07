"""Who is running the pipeline: their lab login, matched to a person in people.json."""

from __future__ import annotations

import getpass
import json
import logging
import os
from functools import lru_cache
from typing import NotRequired, TypedDict

from pipe.core.util.paths import get_production_path

log = logging.getLogger(__name__)


class Person(TypedDict):
    """One record of people.json, which TDs edit by hand."""

    # How the pipeline shows them. It need not match ShotGrid, which is joined
    # on `shotgrid` alone: names there are shared by duplicate accounts and change.
    name: str
    login: NotRequired[str]
    # The ShotGrid HumanUser id.
    shotgrid: NotRequired[int]
    # The Discord user id, which announcements ping.
    discord: NotRequired[str]


def _current_login() -> str:
    try:
        return os.getlogin()
    except OSError:
        return getpass.getuser()


@lru_cache(maxsize=1)
def people() -> list[Person]:
    """Everyone in people.json, or no one when it can't be read."""
    path = get_production_path() / "json" / "people.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s, so everyone shows as their login.", path)
        return []


def current_person() -> Person | None:
    login = _current_login().strip().lower()
    return next(
        (p for p in people() if p.get("login", "").lower() == login),
        None,
    )


def resolve_artist_display_name() -> str:
    """The current person's name, or their login when people.json doesn't know them."""
    person = current_person()
    return person["name"] if person else _current_login().strip()


def current_shotgrid_id() -> int | None:
    person = current_person()
    return person.get("shotgrid") if person else None


def discord_ids() -> dict[int, str]:
    """Discord user ids by ShotGrid user id."""
    return {
        p["shotgrid"]: p["discord"]
        for p in people()
        if "shotgrid" in p and "discord" in p
    }


__all__ = [
    "Person",
    "current_person",
    "current_shotgrid_id",
    "discord_ids",
    "people",
    "resolve_artist_display_name",
]
