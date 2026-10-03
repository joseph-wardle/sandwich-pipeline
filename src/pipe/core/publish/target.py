"""What a publish writes, and the steps every DCC's publish takes around the write."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pipe.core.announce import DOWNSTREAM, announce_publish
from pipe.core.shotgrid import Set, Shot, ShotGrid, ShotGridError

from .versions import (
    commit_version,
    copy_source,
    create_staging,
    discard_staged,
    make_current,
    next_version,
    staging_layer_path,
    stamp,
    version_name,
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
    final: int | None

    @property
    def label(self) -> str:
        """How the dialogs name this version, such as `A_210 cfx v005`."""
        return f"{self.name} {version_name(self.version)}"

    @property
    def downstream(self) -> list[str]:
        """The Steps an announcement tells. Empty when there is no one to tell."""
        return DOWNSTREAM[self.department] if self.department is not None else []

    def announced(self, announce: bool, final: bool) -> bool:
        """Whether a publish the artist asked to announce or not, as FINAL or not, is."""
        return announce or final or self.final is not None


def publish_version(
    conn: ShotGrid,
    target: Target,
    *,
    scene: Path,
    author: str,
    note: str,
    announce: bool,
    final: bool,
    write: Callable[[Path], None],
    detail: str = "",
) -> list[str]:
    """Publish `target` from `scene`, which the caller has just saved.

    `write` exports the layer to the path it is given. Whatever it raises stops
    the publish with nothing published. `announce` and `final` are what the
    artist asked; `Target.announced` says whether downstream is told. `detail`
    is what the announcement says besides the version and the note, such as
    which rigs were published. Returns the result box's lines.
    """
    staged = _stage(target)
    try:
        write(staged)
        copy_source(target.current, target.version, scene)
        stamp(target.current, target.version, author=author, note=note, final=final)
    except BaseException:
        _discard(target)
        raise
    return _release(
        conn,
        target,
        author=author,
        note=note,
        action="published as FINAL" if final else "published",
        announce=target.announced(announce, final),
        detail=detail,
    )


def _stage(target: Target) -> Path:
    """Create the folder the version is written into; return its layer's path."""
    try:
        staged = create_staging(target.current, target.version)
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
    # Asked once the folder is held, so no one can publish this number in between.
    if next_version(target.current) != target.version:
        _discard(target)
        raise Refused(
            f"Nothing was published: {version_name(target.version)} was published while "
            "this dialog was open. Publish again to make the next version."
        )
    return staged


def _discard(target: Target) -> None:
    """Delete what was staged.

    A failure is only logged, so it can't hide why the publish stopped.
    """
    try:
        discard_staged(target.current, target.version)
    except OSError:
        staging = staging_layer_path(target.current, target.version).parent
        log.exception("Could not delete %s.", staging)


def _release(
    conn: ShotGrid,
    target: Target,
    *,
    author: str,
    note: str,
    action: str,
    announce: bool,
    detail: str,
) -> list[str]:
    """Make the staged version current, register it in ShotGrid and tell downstream."""
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

    lines = [f"{target.label} {action}."]
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
    if target.department is not None and announce:
        said = ", ".join(filter(None, [version_path.parent.name, detail]))
        lines += announce_publish(
            conn,
            deliverable=cast(Shot, target.entity),
            department=target.department,
            artist=author,
            path=version_path,
            action=action,
            detail=f"{said}: {note}" if note else said,
        )
    return lines
