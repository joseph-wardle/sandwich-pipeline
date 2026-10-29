"""The view toggle: which of their views, such as Flipbook or Render, the
clips on screen play. Switching keeps the clip, the frame and the play state."""

from __future__ import annotations

from collections.abc import Sequence

from Qt.QtCore import Qt, Signal
from Qt.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer import settings

# Shown on a cell whose view isn't on disk yet, as a wedge still rendering.
NOT_YET = "Rendering"


def names(clips: Sequence[PreviewClip]) -> list[str]:
    """The views the toggle offers: those of the first clip that has any."""
    return next(([name for name, _ in c.views] for c in clips if c.views), [])


class ViewToggle(QWidget):
    """Hidden when no clip has views. The choice is remembered between viewers."""

    changed = Signal()

    _names: list[str]
    _buttons: QButtonGroup
    _chosen_id: int

    def __init__(self, view_names: list[str]) -> None:
        super().__init__()
        self._names = view_names
        self._buttons = QButtonGroup(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for index, name in enumerate(view_names):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setToolTip("Switch between the views of these frames (R).")
            # As with the transport buttons, Space must keep playing.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._buttons.addButton(button, index)
            row.addWidget(button)
        remembered = settings.load_view()
        self._chosen_id = (
            view_names.index(remembered) if remembered in view_names else 0
        )
        if view_names:
            self._buttons.button(self._chosen_id).setChecked(True)
        self._buttons.idClicked.connect(lambda _index: self._chosen())
        self.setVisible(bool(view_names))

    @property
    def view(self) -> str:
        return self._names[self._buttons.checkedId()] if self._names else ""

    def offer(self, offered: bool) -> None:
        """A clip without views plays its own frames, shown as the first view.
        The chosen view comes back with the next clip that has views."""
        self.setEnabled(offered)
        if self._names:
            self._buttons.button(self._chosen_id if offered else 0).setChecked(True)

    def cycle(self) -> None:
        following = (self._buttons.checkedId() + 1) % len(self._names)
        self._buttons.button(following).setChecked(True)
        self._chosen()

    def _chosen(self) -> None:
        self._chosen_id = self._buttons.checkedId()
        settings.save_view(self.view)
        self.changed.emit()


__all__ = ["NOT_YET", "ViewToggle", "names"]
