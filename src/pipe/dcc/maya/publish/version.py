"""Publish what a Maya scene exports for a shot as the next publish version (ADR-0028)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import maya.cmds as mc

from pipe.core import telemetry
from pipe.core.publish import copy_source, discard_staged, stamp, version_layer_path
from pipe.core.publish.target import Refused, Target, release, stage
from pipe.core.ui import MessageDialog, PublishChoice
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.util.selection import maintain_selection

from .publisher import Publisher

TITLE = "Publish"


class VersionPublisher(Publisher):
    """A publisher whose export becomes a publish version.

    A Maya scene can't declare what it publishes the way a Houdini publish node
    does, so the subclass asks in `_choose`, in a dialog that ends with the
    publish rows (ADR-0029).
    """

    _target: Target
    _choice: PublishChoice

    def publish(self) -> None:
        try:
            with maintain_selection():
                result = self._publish()
        except Refused as refusal:
            MessageDialog(self._window, str(refusal), TITLE).exec_()
            return
        if result is None:
            return
        MessageDialog(self._window, result, TITLE).exec_()
        if self._choice.playblast:
            label, note = self._target.label, self._choice.note
            self._open_playblast(f"{label}: {note}" if note else label)

    def _publish(self) -> str | None:
        """What the result box says, or None if the artist backed out."""
        if not self._choose():
            return None
        target, note = self._target, self._choice.note
        scene = _save_scene()
        author = resolve_artist_display_name()

        self._publish_path = stage(target)
        try:
            with telemetry.record(
                telemetry.EVENT_PUBLISH_USD,
                payload={
                    "kind": self._PUBLISH_KIND,
                    "publish_path": str(
                        version_layer_path(target.current, target.version)
                    ),
                },
                shot=target.entity,
            ):
                self._mayausd_export_and_finalize()
            copy_source(target.current, target.version, scene)
            stamp(target.current, target.version, author=author, note=note)
        except BaseException:
            discard_staged(target.current, target.version)
            raise

        lines = release(
            self._conn, target, author=author, note=note, detail=self._detail()
        )
        return "\n\n".join(["\n".join(lines), *self._warnings()])

    def _choose(self) -> bool:
        """Ask what to publish, select it, and set `_target` and `_choice`.

        False if the artist backed out or there is nothing to publish, which the
        artist has been told.
        """
        raise NotImplementedError

    def _detail(self) -> str:
        """What the announcement says besides the version and the note."""
        return ""

    def _warnings(self) -> list[str]:
        """Paragraphs the result box adds about what was published."""
        return []

    def _open_playblast(self, description: str) -> None:
        raise NotImplementedError


def _save_scene() -> Path:
    scene = cast(str, mc.file(query=True, sceneName=True))
    if not scene:
        raise Refused(
            "Save the scene first. A publish keeps a copy of the scene that made it."
        )
    try:
        # Saved first, so the scene kept with the version is the one that made it.
        mc.file(save=True, force=True)
    except RuntimeError as exc:
        raise Refused(
            f"The scene couldn't be saved, so nothing was published.\n{exc}"
        ) from None
    return Path(scene)
