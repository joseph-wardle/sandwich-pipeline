from __future__ import annotations

import time
from typing import Callable

import maya.cmds as mc
from Qt.QtCore import QTimer
from Qt.QtWidgets import QWidget

from pipe.dcc.maya.util.time import scene_frame_rate

from .active import active_shot
from .state import FRAME_START, PrevisShot, PrevisState


class CutPlayback:
    """Real-time playback of the cut."""

    def __init__(
        self,
        *,
        state: Callable[[], PrevisState],
        go_to_cut_frame: Callable[[int], None],
        parent: QWidget,
    ) -> None:
        self._state = state
        self._go_to_cut_frame = go_to_cut_frame
        self._frame_due_at = 0.0  # when the frame on screen came due
        # Parented, so closing the panel ends playback with it.
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._tick)

    @property
    def is_playing(self) -> bool:
        return self._timer.isActive()

    def play(self) -> None:
        state = self._state()
        if self.is_playing or not state.shots:
            return
        # Two transports driving `currentTime` would fight over every frame.
        mc.play(state=False)
        shot, source_frame = _scene_position(state)
        # A scene frame no shot covers has no place in the cut; start at its head.
        resume = FRAME_START if shot is None else state.cut_frame(shot, source_frame)
        self._warm(resume=resume)
        self._frame_due_at = time.monotonic()
        fps = scene_frame_rate()
        # Beats at twice the frame rate, so the tick that lands a frame is never
        # more than half a frame late.
        self._timer.start(max(1, round(500 / fps)))

    def stop(self) -> None:
        self._timer.stop()

    def toggle(self) -> None:
        if self.is_playing:
            self.stop()
        else:
            self.play()

    def _tick(self) -> None:
        now = time.monotonic()
        fps = scene_frame_rate()  # read per tick, so changing it mid-play is honest
        frames = int((now - self._frame_due_at) * fps)
        if frames <= 0:
            return  # the timer beats faster than the cut plays; wait for a whole frame
        if frames > fps:
            # More than a second behind: a modal dialog or a heavy rebuild, not a
            # slow frame. Repaying it would skip most of a shot, so resume instead.
            frames = 1
            self._frame_due_at = now
        else:
            self._frame_due_at += frames / fps
        if self._advance(frames):
            # Drawing a shot's first frame is the expensive one — a scene frame
            # Maya has not evaluated, through a camera the monitor has not drawn.
            # Charge it to the clock, or it comes straight back out of that shot.
            self._frame_due_at = time.monotonic()

    def _advance(self, frames: int) -> bool:
        """Step up to `frames` down the cut. True when it landed on a new shot —
        the frame a transition costs."""
        state = self._state()
        shot, source_frame = _scene_position(state)
        if shot is None:
            self._go_to_cut_frame(FRAME_START)  # nothing plays here; rejoin at the head
            return True
        # Stop at the next shot's first frame and no further. Time lost to a slow
        # transition is paid inside the shot that was slow to draw, never out of
        # the head of the shot the artist pressed play to see.
        left_in_shot = shot.source_out - source_frame + 1
        if frames < left_in_shot:
            self._go_to_cut_frame(state.cut_frame(shot, source_frame + frames))
            return False
        if shot is state.shots[-1]:
            self._go_to_cut_frame(FRAME_START)  # the end of the cut is where it loops
        else:
            # One past this shot's last frame is the next shot's first, on the cut.
            self._go_to_cut_frame(state.cut_frame(shot, shot.source_out + 1))
        return True

    def _warm(self, *, resume: int) -> None:
        """Draw every shot's first frame once, then come back to `resume`."""
        mc.inViewMessage(
            assistMessage="Preparing playback…", position="midCenter", fade=False
        )
        try:
            for cut_start in self._state().cut_starts().values():
                self._go_to_cut_frame(cut_start)
                mc.refresh()  # the point is the draw; `currentTime` may coalesce it
        finally:
            mc.inViewMessage(clear="midCenter")
        self._go_to_cut_frame(resume)


def _scene_position(state: PrevisState) -> tuple[PrevisShot | None, int]:
    """The shot playing at scene time, and that scene frame. No shot means a gap."""
    source_frame = int(mc.currentTime(query=True))
    return active_shot(state, source_frame), source_frame


__all__ = ["CutPlayback"]
