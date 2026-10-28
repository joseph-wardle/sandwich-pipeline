"""In-DCC playblast viewer."""

from __future__ import annotations

from collections.abc import Sequence

from Qt.QtWidgets import QWidget

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer.transport import ClipAction
from pipe.core.playblast.viewer.window import ViewerWindow


def open_viewer(
    clips: list[PreviewClip],
    parent: QWidget | None,
    *,
    cut: PreviewClip | None = None,
    cut_unavailable: str = "",
    actions: Sequence[ClipAction] = (),
    missing_frames: str = "",
    runs: Sequence[Sequence[PreviewClip]] = (),
) -> None:
    """Open a viewer window on `clips`. `missing_frames` replaces the playblast
    advice given when a clip's frames can't be read. `runs` groups the clips
    whose frames match, and shows the Grid that plays a group side by side."""
    ViewerWindow(
        clips,
        parent,
        cut=cut,
        cut_unavailable=cut_unavailable,
        actions=actions,
        missing_frames=missing_frames,
        runs=runs,
    ).show()


__all__ = ["ClipAction", "open_viewer"]
