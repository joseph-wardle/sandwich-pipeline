"""In-DCC playblast viewer."""

from __future__ import annotations

from Qt.QtWidgets import QWidget

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer.window import ViewerWindow


def open_viewer(clips: list[PreviewClip], parent: QWidget | None) -> None:
    """Open a viewer window on `clips`"""
    ViewerWindow(clips, parent).show()


__all__ = ["open_viewer"]
