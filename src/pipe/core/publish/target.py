"""What a publish writes, and the steps every DCC's publish takes around the write."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pipe.core.announce import announce_publish
from pipe.core.shot import current_layer_path, published_file_code, shot_root_path
from pipe.core.shotgrid import Set, Shot, ShotGrid, ShotGridError

from .versions import (
    commit_version,
    create_staging,
    make_current,
    next_version,
    staging_layer_path,
)

log = logging.getLogger(__name__)


class Refused(Exception):
    """The publish can't go on. The message tells the artist why and what to do."""


@dataclass(frozen=True)
class Target:
    entity: Shot | Set
    # How the dialogs name what is published, such as `A_210 cfx`.
    name: str
    current: Path
    version: int
    # The ShotGrid PublishedFile's name, the same on every version, and its code.
    file_name: str
    file_code: str
    # Whose downstream is told. None tells no one.
    department: str | None

    @property
    def label(self) -> str:
        """How the dialogs name this version, such as `A_210 cfx v005`."""
        return f"{self.name} v{self.version:03d}"


def shot_target(shot: Shot, department: str, name: str | None = None) -> Target:
    """The next version of a shot department's layer.

    `name` is the layer's name when it isn't the department's, as `anim.spline` is
    for anim's second stream.
    """
    code = cast(str, shot.code)
    name = name or department
    current = current_layer_path(shot_root_path(shot), department, name)
    version = next_version(current)
    return Target(
        entity=shot,
        name=f"{code} {name}",
        current=current,
        version=version,
        file_name=name,
        file_code=published_file_code(code, name, version),
        department=department,
    )


def stage(target: Target) -> Path:
    """Create the folder the version is written into; return its layer's path."""
    try:
        return create_staging(target.current, target.version)
    except FileExistsError as exc:
        raise Refused(
            f"Nothing was published: {exc.filename} already exists. Someone may be "
            f"publishing {target.label} right now. If not, a publish stopped "
            "partway: delete that folder and publish again."
        ) from None
    except OSError:
        log.exception("Could not create the staging folder for %s.", target.label)
        raise Refused(
            f"Couldn't create a folder for {target.label}, so nothing was "
            f"published. Ask a TD to check the permissions on {target.current.parent}."
        ) from None


def release(
    conn: ShotGrid, target: Target, *, author: str, note: str, detail: str = ""
) -> list[str]:
    """Make the staged version current, register it in ShotGrid and tell downstream.

    `detail` is what the announcement says besides the version and the note, such
    as which rigs were published. Returns the result box's lines.
    """
    staging = staging_layer_path(target.current, target.version).parent
    try:
        version_path = commit_version(target.current, target.version)
    except OSError:
        log.exception("Could not rename %s into place.", staging)
        raise Refused(
            f"{target.label} was written to {staging} but couldn't be moved into "
            "place, so nothing was published. Ask a TD."
        ) from None
    try:
        make_current(target.current, target.version)
    except OSError:
        log.exception("Could not make %s current.", target.label)
        raise Refused(
            f"{target.label} is published, but it isn't current, so nothing reads "
            "it yet. A TD can run pipe.core.publish.make_current on "
            f"{target.current} with version {target.version}."
        ) from None

    lines = [f"Published {target.label}."]
    try:
        conn.create_published_file(
            target.entity,
            name=target.file_name,
            code=target.file_code,
            version=target.version,
            path=version_path,
            description=note,
        )
    except ShotGridError:
        log.exception("Could not register %s in ShotGrid.", target.label)
        lines.append("ShotGrid: the version wasn't registered. Tell a TD.")
    if target.department is not None:
        said = ", ".join(filter(None, [version_path.parent.name, detail]))
        lines += announce_publish(
            conn,
            deliverable=cast(Shot, target.entity),
            department=target.department,
            artist=author,
            path=version_path,
            detail=f"{said}: {note}" if note else said,
        )
    return lines
