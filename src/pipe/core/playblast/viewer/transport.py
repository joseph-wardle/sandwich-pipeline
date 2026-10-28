"""The controls under the video: scrub bar, play buttons, clip actions, grid."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import attrs

from Qt.QtCore import QSize, Qt
from Qt.QtGui import QFontDatabase, QIcon, QPalette
from Qt.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer import icons, style
from pipe.core.playblast.viewer.scrub import TimelineSlider

_STEP_BUTTON = 34
_STEP_ICON = 16
_PLAY_ICON = 26


@attrs.frozen
class ClipAction:
    """A button that acts on the clip on screen. `run` returns whether the
    clip is gone, such as deleted from disk, so the viewer drops it."""

    label: str
    run: Callable[[PreviewClip], bool]
    tooltip: str = ""


class Transport(QWidget):
    """Only builds the controls; the window connects them."""

    scrub: TimelineSlider
    step_back: QToolButton
    play: QToolButton
    step_forward: QToolButton
    loop: QCheckBox
    grid: QPushButton
    page_back: QToolButton
    page_forward: QToolButton
    page_label: QLabel
    _start_label: QLabel
    _end_label: QLabel
    _play_icon: QIcon
    _pause_icon: QIcon

    def __init__(
        self,
        actions: Sequence[ClipAction],
        run_action: Callable[[ClipAction], None],
        *,
        grid: bool,
        views: QWidget,
    ) -> None:
        super().__init__()
        transport = QVBoxLayout(self)
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(style.GAP)

        self.scrub = TimelineSlider()
        self.scrub.setToolTip("Home and End jump to the first and last frame.")
        self.scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Start/end frames flank the bar so its ends read as the clip's
        # range; the current frame is drawn on the handle by the slider itself.
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._start_label = QLabel("")
        self._end_label = QLabel("")
        for label in (self._start_label, self._end_label):
            label.setFont(fixed)
        scrub_row = QHBoxLayout()
        scrub_row.setSpacing(style.PAD_S)
        scrub_row.addWidget(self._start_label)
        scrub_row.addWidget(self.scrub, stretch=1)
        scrub_row.addWidget(self._end_label)
        transport.addLayout(scrub_row)

        icon_color = self.palette().color(QPalette.ColorRole.ButtonText).name()
        self._play_icon = icons.play(icon_color, _PLAY_ICON)
        self._pause_icon = icons.pause(icon_color, _PLAY_ICON)

        row = QHBoxLayout()
        row.setSpacing(style.GAP)

        # Equal-stretch side sections keep the play cluster dead-center; the
        # left one balances the loop toggle on the right.
        left = QHBoxLayout()
        for action in actions:
            left.addWidget(_action_button(action, run_action))
        left.addStretch(1)
        row.addLayout(left, stretch=1)

        self.step_back = _button(
            icons.step_back(icon_color, _STEP_ICON), "Step one frame back (Left)"
        )
        self.play = _button(self._play_icon, "Play/pause (Space)", primary=True)
        self.step_forward = _button(
            icons.step_forward(icon_color, _STEP_ICON),
            "Step one frame forward (Right)",
        )
        for button in (self.step_back, self.play, self.step_forward):
            row.addWidget(button)

        right = QHBoxLayout()
        right.addStretch(1)
        self.page_back = _button(
            icons.step_back(icon_color, _STEP_ICON), "Previous page (Page Up)"
        )
        self.page_label = QLabel("")
        self.page_forward = _button(
            icons.step_forward(icon_color, _STEP_ICON), "Next page (Page Down)"
        )
        self.grid = QPushButton("Grid")
        self.grid.setCheckable(True)
        self.grid.setToolTip(
            "Play this run's wedges side by side (G). Click one, or press its "
            "number, to see it alone."
        )
        self.grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grid.setVisible(grid)
        for widget in (self.page_back, self.page_label, self.page_forward):
            widget.setVisible(False)
            right.addWidget(widget)
        right.addWidget(views)
        right.addWidget(self.grid)
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.loop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right.addWidget(self.loop)
        row.addLayout(right, stretch=1)

        transport.addLayout(row)

    def set_range(self, start: int, end: int) -> None:
        self.scrub.setRange(start, end)
        self._start_label.setText(str(start))
        self._end_label.setText(str(end))

    def set_playing(self, playing: bool) -> None:
        self.play.setIcon(self._pause_icon if playing else self._play_icon)

    def set_page(self, text: str) -> None:
        """Shows the page controls with `text`, or hides them when empty."""
        self.page_label.setText(text)
        for widget in (self.page_back, self.page_label, self.page_forward):
            widget.setVisible(bool(text))


def _action_button(
    action: ClipAction, run_action: Callable[[ClipAction], None]
) -> QPushButton:
    button = QPushButton(action.label)
    button.setToolTip(action.tooltip)
    # As with the transport buttons, Space must keep playing.
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.clicked.connect(lambda: run_action(action))
    return button


def _button(icon: QIcon, tooltip: str, *, primary: bool = False) -> QToolButton:
    button = QToolButton()
    button.setIcon(icon)
    if primary:
        # The play button keeps the native raised look (the flat step
        # buttons flank it) and a bigger hit area.
        button.setIconSize(QSize(_PLAY_ICON, _PLAY_ICON))
        button.setFixedSize(style.TRANSPORT_PLAY_SIZE, style.TRANSPORT_PLAY_SIZE)
    else:
        button.setAutoRaise(True)
        button.setIconSize(QSize(_STEP_ICON, _STEP_ICON))
        button.setFixedSize(_STEP_BUTTON, _STEP_BUTTON)
    # Clicking a focusable button would leave focus on it, and the next
    # Space would press it again instead of playing.
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setToolTip(tooltip)
    return button


__all__ = ["ClipAction", "Transport"]
