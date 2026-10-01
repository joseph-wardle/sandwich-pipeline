"""Announcements: tell downstream a publish published."""

from __future__ import annotations

import logging
from pathlib import Path

from env import discord_publish_webhook, discord_role_ids

from pipe.core.shotgrid import Shot, ShotGrid, User

from .discord import post_message
from .shotgrid import send_note

log = logging.getLogger(__name__)

DOWNSTREAM: dict[str, list[str]] = {
    "anim": ["CFX", "FX", "Lighting"],
    "cam": ["Animation", "Lighting"],
}


def announce_publish(
    conn: ShotGrid,
    *,
    shot: Shot,
    department: str,
    artist: str,
    path: Path,
    detail: str = "",
) -> list[str]:
    """Tell `department`'s downstream that `artist` published `shot`.

    `detail` is free text for the message, such as which rigs were published.
    """
    steps = DOWNSTREAM[department]
    subject = f"{shot.code} {department} published"
    sentence = f"{subject} by {artist}"
    if detail:
        sentence += f" ({detail})"
    return [
        _announce_on_discord(sentence, steps),
        _announce_on_shotgrid(
            conn, shot, steps, subject, f"{sentence}\n{path}", artist
        ),
    ]


def _announce_on_discord(sentence: str, steps: list[str]) -> str:
    if discord_publish_webhook is None:
        log.debug("Discord announcements are not configured in env.py")
        return "Discord: not configured, nothing posted."
    tagged = [step for step in steps if step in discord_role_ids]
    try:
        post_message(
            discord_publish_webhook,
            sentence,
            [discord_role_ids[step] for step in tagged],
        )
    except Exception as exc:
        log.exception("Discord announcement failed")
        return (
            f"Discord: could not post ({exc}). "
            f"Tell {_and(steps)} yourself, and check the console."
        )
    line = (
        f"Discord: told {_and(tagged)}."
        if tagged
        else "Discord: posted, tagging no one."
    )
    if untagged := [step for step in steps if step not in tagged]:
        line += f" No Discord role is configured for {_and(untagged)}."
    return line


def _announce_on_shotgrid(
    conn: ShotGrid,
    shot: Shot,
    steps: list[str],
    subject: str,
    body: str,
    artist: str,
) -> str:
    try:
        assignees = send_note(
            conn, shot=shot, steps=steps, subject=subject, body=body, author_name=artist
        )
    except Exception as exc:
        log.exception("ShotGrid announcement failed")
        return (
            f"ShotGrid: could not write the note ({exc}). "
            f"Tell {_and(steps)} yourself, and check the console."
        )
    told = [
        f"{_name(user)} ({step})" for step, users in assignees.items() for user in users
    ]
    line = f"ShotGrid: noted {_and(told)}." if told else "ShotGrid: note written."
    if unassigned := [step for step, users in assignees.items() if not users]:
        line += (
            f" No one is assigned to {_and(unassigned)} on {shot.code}, "
            "so nobody was told for it."
        )
    return line


def _name(user: User) -> str:
    return user.name or f"user {user.id}"


def _and(items: list[str]) -> str:
    """'CFX', 'CFX and FX', 'CFX, FX and Lighting'."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


__all__ = ["DOWNSTREAM", "announce_publish"]
