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
    version_name,
    versions,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Entry:
    target: Target
    info: VersionInfo
    is_current: bool

    @property
    def label(self) -> str:
        """The name its publish gave it, such as `A_210 cfx v003`."""
        return f"{self.target.name} {version_name(self.info.version)}"


def history(target: Target) -> list[Entry]:
    """Every version published to the target, newest first."""
    now = current_version(target.current)
    return [
        Entry(target, version_info(target.current, version), version == now)
        for version in versions(target.current)
    ]


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
    # As loud as a publish of this layer would be.
    if target.department is not None and target.final is not None:
        lines += announce_publish(
            conn,
            deliverable=cast(Shot, target.entity),
            department=target.department,
            artist=author,
            path=version_layer_path(target.current, version),
            action=f"moved to {version_name(version)}",
            detail=f"{target.file_name} was {version_name(was)}"
            if was is not None
            else "",
        )
    return lines
