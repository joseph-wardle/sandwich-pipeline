"""Publish versions as a shot's or set's history: listing them, opening one, undoing one."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pipe.core.announce import announce_publish
from pipe.core.shotgrid import Shot, ShotGrid

from .target import Refused, Target
from .versions import (
    VersionInfo,
    current_version,
    make_current,
    version_info,
    version_layer_path,
    versions,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Entry:
    target: Target
    info: VersionInfo
    current: bool

    @property
    def label(self) -> str:
        """The name its publish gave it, such as `A_210 cfx v003`."""
        return f"{self.target.name} v{self.info.version:03d}"


def history(targets: list[Target]) -> list[Entry]:
    """Every version published to the targets, newest first.

    More than one target is for layers that share a publish folder, as anim's two
    streams do.
    """
    entries = []
    for target in targets:
        published = versions(target.current)
        if not published:
            continue
        now = current_version(target.current)
        entries += [
            Entry(target, version_info(target.current, version), version == now)
            for version in published
        ]
    return sorted(entries, key=lambda entry: entry.info.version, reverse=True)


def replace_scene(entry: Entry, scene: Path) -> None:
    """Overwrite a working scene with the one that made this version."""
    # The dialog offers Open only on a version that kept its scene.
    source = cast(Path, entry.info.source)
    try:
        shutil.copyfile(source, scene)
    except OSError as exc:
        log.exception("Could not copy %s over %s.", source, scene)
        raise Refused(
            f"{scene} couldn't be replaced with the one that made {entry.label}: "
            f"{exc.strerror}. Ask a TD."
        ) from None


def move_current(conn: ShotGrid, entry: Entry, *, author: str) -> list[str]:
    """Point the current layer at this version and tell downstream.

    How a publish is undone. Returns the result box's lines.
    """
    target, version = entry.target, entry.info.version
    was = current_version(target.current)
    try:
        make_current(target.current, version)
    except OSError:
        log.exception("Could not make %s current.", entry.label)
        raise Refused(
            f"{entry.label} couldn't be made current, so nothing changed. Ask a TD "
            f"to check the permissions on {target.current.parent}."
        ) from None

    lines = [f"{entry.label} is current."]
    if target.department is not None:
        lines += announce_publish(
            conn,
            deliverable=cast(Shot, target.entity),
            department=target.department,
            artist=author,
            path=version_layer_path(target.current, version),
            action=f"moved to v{version:03d}",
            detail=f"{target.file_name} was v{was:03d}" if was is not None else "",
        )
    return lines
