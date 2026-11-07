"""Announcements: tell the people downstream that a publish published."""

from __future__ import annotations

import logging
from pathlib import Path

from env import discord_publish_webhooks, discord_role_ids
from pipe.core.shotgrid import Asset, Shot, ShotGrid, User
from pipe.core.util.text import and_list as _and
from pipe.core.util.users import discord_ids, resolve_artist_display_name

from .discord import post_message
from .shotgrid import find_assignees, send_note

log = logging.getLogger(__name__)

DOWNSTREAM: dict[str, list[str]] = {
    "model": ["Rigging", "Shading"],
    "rig": ["Animation"],
    "anim": ["CFX", "FX", "Lighting"],
    "cam": ["Animation", "Lighting"],
    "flo": ["CFX", "FX", "Lighting"],
    "cfx": ["Lighting"],
    "fx": ["Lighting"],
    # Not a real department. This row goes when envfx does.
    "envfx": ["Lighting"],
    # ShotGrid has no comp Step to tell.
    "lighting": [],
}

# Every animator uses a rig, not only the Anim Test assignees on its Asset, so a
# rig post pings the downstream Steps' roles instead of people.
PINGS_ROLES = {"rig"}


def announce_publish(
    conn: ShotGrid,
    *,
    deliverable: Shot | Asset,
    deliverable_name: str | None = None,
    department: str,
    steps: list[str] | None = None,
    artist: str | None = None,
    path: Path,
    announce_path: bool = False,
    action: str = "published",
    detail: str = "",
    discord: bool = True,
    shotgrid: bool = True,
) -> list[str]:
    """Tell `department`'s downstream that `artist` published `shot`/`asset`.

    `action` is what the artist did when it wasn't a publish, as Make Current's
    `moved to v003` is. `detail` is free text for the message, such as which rigs
    were published. `steps` tells only those of the department's downstream.
    Returns one line per channel, or none for a department with no downstream.
    """
    steps = DOWNSTREAM[department] if steps is None else steps
    if not steps:
        return []
    resolved_artist = artist if artist else resolve_artist_display_name()
    resolved_deliverable_name = deliverable_name or deliverable.code
    subject = f"{resolved_deliverable_name} {department} {action}"
    sentence = f"{subject} by {resolved_artist}"
    if announce_path:
        sentence += f" to `{path}`"
    if detail:
        sentence += f" ({detail})"
    assignees: dict[str, list[User]] | None = None
    if shotgrid or (discord and department not in PINGS_ROLES):
        try:
            assignees = find_assignees(conn, deliverable, steps)
        except Exception:
            log.exception("Could not look up who is assigned downstream")
    results: list[str] = []
    if discord:
        results.append(_announce_on_discord(department, sentence, steps, assignees))
    if shotgrid:
        results.append(
            _announce_on_shotgrid(
                conn, deliverable, steps, assignees, subject, f"{sentence}\n{path}"
            )
        )
    return results


def _announce_on_discord(
    department: str,
    sentence: str,
    steps: list[str],
    assignees: dict[str, list[User]] | None,
) -> str:
    webhook = discord_publish_webhooks.get(department)
    if webhook is None:
        log.debug("env.py has no Discord webhook for %s publishes", department)
        return (
            f"Discord: no channel is set up for {department} publishes, nothing posted."
        )
    if department in PINGS_ROLES:
        tagged = [step for step in steps if step in discord_role_ids]
        line = (
            f"Discord: told {_and(tagged)}."
            if tagged
            else "Discord: posted, tagging no one."
        )
        if untagged := [step for step in steps if step not in tagged]:
            line += f" No Discord role is configured for {_and(untagged)}."
        roles = [discord_role_ids[step] for step in tagged]
        return _post(webhook, sentence, steps, line, role_ids=roles, user_ids=[])
    if assignees is None:
        line = "Discord: posted, pinging no one, because ShotGrid couldn't say who is assigned."
        return _post(webhook, sentence, steps, line, role_ids=[], user_ids=[])
    ids = discord_ids()
    people = _everyone(assignees)
    pinged = [user for user in people if user.id in ids]
    unpinged = [user for user in people if user.id not in ids]
    line = (
        f"Discord: pinged {_and([_name(user) for user in pinged])}."
        if pinged
        else "Discord: posted, pinging no one."
    )
    if unpinged:
        log.warning(
            "people.json has no Discord id for ShotGrid users %s",
            ", ".join(f"{_name(user)} ({user.id})" for user in unpinged),
        )
        line += (
            " Not pinged, with no Discord id on file: "
            f"{_and([_name(user) for user in unpinged])}."
        )
    # Named, so the post still says who it is for.
    text = " ".join([*(_name(user) for user in unpinged), sentence])
    users = [ids[user.id] for user in pinged]
    return _post(webhook, text, steps, line, role_ids=[], user_ids=users)


def _post(
    webhook: str,
    text: str,
    steps: list[str],
    line: str,
    *,
    role_ids: list[str],
    user_ids: list[str],
) -> str:
    """Post `text`, and return `line`, or why nothing was posted."""
    try:
        post_message(webhook, text, role_ids=role_ids, user_ids=user_ids)
    except Exception as exc:
        log.exception("Discord announcement failed")
        return (
            f"Discord: could not post ({exc}). "
            f"Tell {_and(steps)} yourself, and check the console."
        )
    return line


def _announce_on_shotgrid(
    conn: ShotGrid,
    deliverable: Shot | Asset,
    steps: list[str],
    assignees: dict[str, list[User]] | None,
    subject: str,
    body: str,
) -> str:
    if assignees is None:
        return (
            "ShotGrid: couldn't look up who is assigned, so no note was written. "
            f"Tell {_and(steps)} yourself, and check the console."
        )
    try:
        send_note(
            conn,
            deliverable=deliverable,
            to=_everyone(assignees),
            subject=subject,
            body=body,
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
            f" No one is assigned to {_and(unassigned)} on {deliverable.code}, "
            "so nobody was told for it."
        )
    return line


def _everyone(assignees: dict[str, list[User]]) -> list[User]:
    """Each assignee once, though they may work on several of the Steps."""
    return list(
        {user.id: user for users in assignees.values() for user in users}.values()
    )


def _name(user: User) -> str:
    return user.name or f"user {user.id}"


__all__ = ["DOWNSTREAM", "announce_publish"]
