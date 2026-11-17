"""Play a product's frames, through the display transform, at the shot's rate.

A frame takes about half a second to decode, so frames are decoded ahead of
the playhead on several threads and kept as display images. The first loop
plays as fast as they arrive, waiting rather than skipping; later loops play
from memory at full rate, as Nuke and RV do. Changing the AOV, view or
exposure starts the buffer again.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Qt import QtCore, QtGui

from pipe.core.render.network import frames
from pipe.core.render.network.frames import ChannelGroup

DEFAULT_FPS = 24.0
# A decode holds every channel of its frame, about 300 MB for a beauty, so
# eight at once is the most a workstation should hold.
_DECODERS = max(2, min(8, (os.cpu_count() or 4) // 2))
# About 6 MB a frame at 1920 × 1080: 240 frames, enough for any shot.
_CACHE_BYTES = 1_500_000_000


@dataclass(frozen=True)
class Look:
    """What a frame is shown through; a change makes every buffered frame stale."""

    group: ChannelGroup
    view: str
    exposure: float


class _Decoded(QtCore.QObject):
    done = QtCore.Signal(int, int, object)
    failed = QtCore.Signal(int, int, str)


class _Decode(QtCore.QRunnable):
    def __init__(
        self, generation: int, frame: int, path: Path, look: Look, signals: _Decoded
    ) -> None:
        super().__init__()
        self._job = (generation, frame, path, look)
        self._signals = signals

    def run(self) -> None:
        generation, frame, path, look = self._job
        try:
            shown = frames.display(
                frames.read(path, look.group), look.view, look.exposure
            )
            height, width, _ = shown.shape
            # Made here rather than on the window's thread; QImage allows that.
            image = QtGui.QImage(
                shown.tobytes(), width, height, 3 * width, QtGui.QImage.Format_RGB888
            ).copy()
        except Exception as exc:
            self._report(self._signals.failed, generation, frame, f"{path.name}: {exc}")
            return
        self._report(self._signals.done, generation, frame, image)

    @staticmethod
    def _report(signal: Any, *values: object) -> None:
        try:
            signal.emit(*values)
        except RuntimeError:
            # The window closed while this decoded; no one is left to show it.
            pass


class Player(QtCore.QObject):
    """Plays the frames it is given in order, looping.

    `shown` carries each frame number and its image as it plays; `playing`
    whether it plays; `status` what it is doing, such as buffering.
    """

    shown = QtCore.Signal(int, object)
    playing = QtCore.Signal(bool)
    status = QtCore.Signal(str)

    _frames: dict[int, Path]
    _order: list[int]
    _look: Look | None
    # Display images by frame for the current look, oldest use first.
    _cache: OrderedDict[int, QtGui.QImage]
    _cache_bytes: int
    _pending: set[int]
    # Bumped when the frames or look change, so late decodes are dropped.
    _generation: int
    _position: int
    _fps: float
    _timer: QtCore.QTimer
    _pool: QtCore.QThreadPool
    _signals: _Decoded
    # When each recent frame was shown, for the rate actually reached.
    _ticks: list[float]

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._frames, self._order, self._look = {}, [], None
        self._cache, self._cache_bytes, self._pending = OrderedDict(), 0, set()
        self._generation, self._position, self._fps = 0, 0, DEFAULT_FPS
        self._ticks = []
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(_DECODERS)
        self._signals = _Decoded(self)
        self._signals.done.connect(self._decoded)
        self._signals.failed.connect(self._failed)
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.set_fps(DEFAULT_FPS)

    # ---- control -------------------------------------------------------

    def is_playing(self) -> bool:
        return self._timer.isActive()

    def set_frames(self, by_frame: dict[int, Path]) -> None:
        if by_frame != self._frames:
            self._frames, self._order = dict(by_frame), sorted(by_frame)
            self._reset()

    def set_look(self, look: Look) -> None:
        if look != self._look:
            self._look = look
            self._reset()

    def set_fps(self, fps: float) -> None:
        self._fps = max(fps, 1.0)
        self._timer.setInterval(max(int(round(1000 / self._fps)), 1))

    def play(self, from_frame: int) -> None:
        if not self._order or self._look is None:
            return
        self._position = self._index_of(from_frame)
        self._ticks = []
        self._request_ahead()
        self._timer.start()
        self.playing.emit(True)

    def pause(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.playing.emit(False)
            self.status.emit(self._buffered_text())

    def clear(self) -> None:
        self.pause()
        self._frames, self._order = {}, []
        self._reset()

    # ---- playing -------------------------------------------------------

    def _tick(self) -> None:
        frame = self._order[self._position]
        image = self._cache.get(frame)
        self._request_ahead()
        if image is None:
            # Wait for it rather than skip it, so every frame is seen.
            self.status.emit(f"Buffering… {self._buffered_text()}")
            return
        self._cache.move_to_end(frame)
        self.shown.emit(frame, image)
        self._position = (self._position + 1) % len(self._order)
        now = time.monotonic()
        self._ticks = [*self._ticks[-23:], now]
        if len(self._ticks) > 1:
            rate = (len(self._ticks) - 1) / (self._ticks[-1] - self._ticks[0])
            self.status.emit(
                f"Playing {rate:.1f} of {self._fps:g} fps · {self._buffered_text()}"
            )

    def _request_ahead(self) -> None:
        """Keep every decoder busy with the next frames not yet buffered."""
        if self._look is None:
            return
        count = len(self._order)
        for step in range(count):
            if len(self._pending) >= _DECODERS:
                return
            frame = self._order[(self._position + step) % count]
            if frame in self._cache or frame in self._pending:
                continue
            if step and self._cache_bytes >= _CACHE_BYTES:
                # Full: what is ahead would only push out what is about to play.
                return
            self._pending.add(frame)
            self._pool.start(
                _Decode(
                    self._generation,
                    frame,
                    self._frames[frame],
                    self._look,
                    self._signals,
                )
            )

    def _decoded(self, generation: int, frame: int, image: QtGui.QImage) -> None:
        if generation != self._generation:
            return
        self._pending.discard(frame)
        self._cache[frame] = image
        self._cache_bytes += image.sizeInBytes()
        while self._cache_bytes > _CACHE_BYTES and len(self._cache) > 1:
            _, dropped = self._cache.popitem(last=False)
            self._cache_bytes -= dropped.sizeInBytes()
        if self.is_playing():
            self._request_ahead()

    def _failed(self, generation: int, frame: int, message: str) -> None:
        if generation != self._generation:
            return
        self._pending.discard(frame)
        # A frame that can't be read would stall playback forever.
        self._frames.pop(frame, None)
        self._order = sorted(self._frames)
        self._position = min(self._position, max(len(self._order) - 1, 0))
        self.status.emit(f"Skipped unreadable {message}")
        if not self._order:
            self.pause()

    # ---- helpers -------------------------------------------------------

    def _reset(self) -> None:
        self._generation += 1
        self._cache.clear()
        self._cache_bytes = 0
        self._pending.clear()
        self._ticks = []

    def _index_of(self, frame: int) -> int:
        """The frame, or the last one before it, as the viewer shows a gap."""
        earlier = [i for i, f in enumerate(self._order) if f <= frame]
        return earlier[-1] if earlier else 0

    def _buffered_text(self) -> str:
        return f"{len(self._cache)}/{len(self._order)} frames buffered"
