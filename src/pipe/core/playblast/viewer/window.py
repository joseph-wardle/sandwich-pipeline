"""The viewer window: frame playback, clip list, and the run grid."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from Qt.QtCore import QElapsedTimer, Qt, QTimer
from Qt.QtGui import QCloseEvent, QImageReader, QKeyEvent, QKeySequence
from Qt.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.playblaster import DEFAULT_RESOLUTION
from pipe.core.playblast.viewer import confirm_column, filmstrip, grid, style, views
from pipe.core.playblast.viewer.confirm_column import ConfirmColumn
from pipe.core.playblast.viewer.transport import ClipAction, Transport

# One clip alone, a page of its run's grid, or one wedge entered from the grid.
Mode = Literal["single", "grid", "entered"]

# The number keys that enter the cells of a page, in order.
_CELL_KEYS = [getattr(Qt.Key, f"Key_{number}") for number in range(1, grid.PAGE + 1)]


def _frame_size(clip: PreviewClip) -> tuple[int, int] | None:
    """Pixel size of the clip's first frame, read from the PNG header alone,
    or None if the frame is unreadable."""
    size = QImageReader(str(clip.frame_path(clip.frame_start))).size()
    if size.width() > 0 and size.height() > 0:
        return size.width(), size.height()
    return None


class ViewerWindow(QMainWindow):
    _clips: list[PreviewClip]
    _runs: list[list[PreviewClip]]
    _resolution: tuple[int, int]
    _current_index: int
    _frame: int
    _shown: list[PreviewClip]
    _mode: Mode
    _playing: bool
    _was_playing_before_scrub: bool
    _missing_frames_warned: set[PreviewClip]
    _clock: QElapsedTimer
    _clock_frame: int
    _ticker: QTimer
    _canvas: grid.Canvas
    _sidebar: filmstrip.Sidebar
    _transport: Transport
    _views: views.ViewToggle
    _confirm: ConfirmColumn
    _actions: Sequence[ClipAction]
    _missing_frames: str

    def __init__(
        self,
        clips: list[PreviewClip],
        parent: QWidget | None,
        *,
        cut: PreviewClip | None = None,
        cut_unavailable: str = "",
        actions: Sequence[ClipAction] = (),
        missing_frames: str = "",
        runs: Sequence[Sequence[PreviewClip]] = (),
    ) -> None:
        # The parent (the DCC main window) owns the viewer's lifetime; a
        # QMainWindow always carries Qt.Window, so it stays a real window
        # rather than embedding. DeleteOnClose frees it — nobody keeps a
        # reference.
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Copied, since an action can remove a clip.
        self._clips = list(clips)
        self._runs = [list(run) for run in runs]
        # Sizing only — an unreadable first frame (reported per clip on
        # playback) still deserves a sanely sized window.
        self._resolution = _frame_size(clips[0]) or DEFAULT_RESOLUTION
        self._current_index = 0
        self._frame = clips[0].frame_start
        self._shown = []
        self._mode = "grid" if self._runs else "single"
        self._playing = False
        self._was_playing_before_scrub = False
        self._missing_frames_warned = set()
        self._actions = actions
        self._missing_frames = missing_frames or (
            "The preview's temp files may have been cleaned up — close the "
            "viewer and playblast again."
        )

        # Playback clock: frames advance by wall-clock time, not tick count,
        # so a slow paint skips frames instead of slowing the clip down.
        self._clock = QElapsedTimer()
        self._clock_frame = self._frame
        self._ticker = QTimer(self)
        self._ticker.setTimerType(Qt.TimerType.PreciseTimer)
        self._ticker.timeout.connect(self._on_tick)

        # A cut of one clip is that clip, so the section only earns its space
        # once the window holds a batch.
        self._confirm = ConfirmColumn(
            self._clips,
            cut=cut if len(clips) > 1 else None,
            cut_unavailable=cut_unavailable if len(clips) > 1 else "",
        )
        self._confirm.state_changed.connect(self._refresh_sidebar)
        self._confirm.delivered.connect(self._on_clip_delivered)

        self._build_ui()
        self._size_to_video()

        self._load_clip(0)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(style.PAD_M, style.PAD_M, style.PAD_M, style.PAD_M)
        layout.setSpacing(style.PAD_M)

        self._sidebar = filmstrip.Sidebar(self._clips, self._resolution)
        self._sidebar.clips.currentRowChanged.connect(self._go_to_clip)
        self._sidebar.confirm_remaining.clicked.connect(self._confirm.confirm_remaining)
        layout.addWidget(self._sidebar)

        self._canvas = grid.Canvas()
        self._canvas.clicked.connect(self._on_canvas_clicked)
        # The video holds focus, so the transport keys work the moment the
        # window opens and clicking the video takes them back from a text
        # field. Left to itself Qt would focus the first field instead.
        self._canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._canvas.setFocus()

        self._views = views.ViewToggle(views.names(self._clips))
        self._views.changed.connect(lambda: self._show_frame(self._frame))
        self._transport = Transport(
            self._actions, self._run_action, grid=bool(self._runs), views=self._views
        )
        self._transport.scrub.sliderMoved.connect(self._on_scrub_moved)
        self._transport.scrub.sliderPressed.connect(self._on_scrub_pressed)
        self._transport.scrub.sliderReleased.connect(self._on_scrub_released)
        self._transport.step_back.clicked.connect(
            lambda: self._go_to_frame(self._frame - 1)
        )
        self._transport.play.clicked.connect(self._toggle_playback)
        self._transport.step_forward.clicked.connect(
            lambda: self._go_to_frame(self._frame + 1)
        )
        self._transport.grid.clicked.connect(self._on_grid)
        self._transport.page_back.clicked.connect(lambda: self._turn_page(-1))
        self._transport.page_forward.clicked.connect(lambda: self._turn_page(1))

        player_column = QVBoxLayout()
        player_column.setSpacing(style.GAP)
        player_column.addWidget(self._canvas, stretch=1)
        player_column.addWidget(self._transport)
        layout.addLayout(player_column, stretch=1)

        layout.addWidget(self._confirm)
        self._refresh_sidebar()

        # Confirm is a shortcut rather than a keyPressEvent branch: a focused
        # QLineEdit ignores Return, so the window would confirm *and* the field
        # would fire its own returnPressed. A shortcut runs before either.
        # Key_Enter is the keypad's, and needs its own sequence to match.
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(sequence), self, self._confirm_current)

    def _run_action(self, action: ClipAction) -> None:
        index = self._current_index
        removed = action.run(self._clips[index])
        # A host dialog the action shows, such as Houdini's displayMessage, is
        # the main window's, which comes back in front of the viewer once it
        # closes.
        self.raise_()
        self.activateWindow()
        if not removed:
            return
        if len(self._clips) == 1:
            self.close()
            return
        # Its run's next wedge, or the one before the last, keeps the frame.
        run = self._run()
        position = run.index(self._clips[index])
        neighbour = run[position + 1 : position + 2] or run[position - 1 : position]
        self._clips.pop(index)
        self._confirm.remove(index)
        self._sidebar.remove(index)
        self._refresh_sidebar()
        if neighbour:
            index = self._clips.index(neighbour[0])
        self._load_clip(min(index, len(self._clips) - 1), keep_frame=bool(neighbour))

    def _size_to_video(self) -> None:
        """Sized so the canvas shows a frame at its own resolution. The
        window's sizeHint can't say, since the canvas ignores its own."""
        width, height = self._resolution
        width += style.PAD_M * 2
        if len(self._clips) > 1:
            width += filmstrip.SIDEBAR_WIDTH + style.PAD_M
        if not self._confirm.isHidden():
            width += confirm_column.WIDTH + style.PAD_M
        height += style.PAD_M * 2 + style.GAP + self._transport.sizeHint().height()
        screen = self.screen().availableGeometry()
        self.resize(min(width, screen.width() - 80), min(height, screen.height() - 80))

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _fps(self) -> int:
        return max(1, self._clip().fps)

    def _clip(self) -> PreviewClip:
        return self._clips[self._current_index]

    def _load_clip(self, index: int, *, keep_frame: bool = False) -> None:
        """`keep_frame` keeps the frame and play state, for a clip of the
        same run, whose frame range is the same."""
        self._current_index = index
        clip = self._clip()
        self._ticker.setInterval(max(1, round(1000 / self._fps())))
        self.setWindowTitle(f"Playblast Viewer — {clip.label}")
        self._sidebar.select(index)
        self._confirm.show_clip(index)
        self._transport.set_range(clip.frame_start, clip.frame_end)
        self._views.offer(bool(clip.views))
        if not keep_frame:
            self._frame = clip.frame_start
        self._refresh_view()
        if not keep_frame:
            self._play()

    def _show_frame(self, frame: int) -> None:
        clip = self._clip()
        frame = min(max(frame, clip.frame_start), clip.frame_end)
        paths = [clip.frame_path(frame, self._views.view) for clip in self._shown]
        # A view that isn't on disk yet, as a wedge still rendering.
        notes = [
            views.NOT_YET if clip.views and not path.parent.is_dir() else ""
            for clip, path in zip(self._shown, paths, strict=True)
        ]
        images = grid.load(paths)
        missing = [
            shown
            for shown, image, note in zip(self._shown, images, notes, strict=True)
            if image.isNull() and not note and shown not in self._missing_frames_warned
        ]
        if missing:
            self._warn_missing_frames(missing, frame)
        self._frame = frame
        self._canvas.show_frames(images, notes)
        # The slider repaints its handle readout from this value.
        self._transport.scrub.setValue(frame)

    def _toggle_playback(self) -> None:
        if self._playing:
            self._pause()
        else:
            self._play()

    def _play(self) -> None:
        # Playing from the last frame restarts; anywhere else resumes.
        if self._frame >= self._clip().frame_end:
            self._show_frame(self._clip().frame_start)
        self._playing = True
        self._clock_frame = self._frame
        self._clock.start()
        self._ticker.start()
        self._transport.set_playing(True)

    def _pause(self) -> None:
        self._playing = False
        self._ticker.stop()
        self._transport.set_playing(False)

    def _on_tick(self) -> None:
        clip = self._clip()
        frame = self._clock_frame + self._clock.elapsed() * self._fps() // 1000
        if frame > clip.frame_end:
            self._on_clip_end()
        elif frame != self._frame:
            self._show_frame(frame)

    def _on_clip_end(self) -> None:
        if self._transport.loop.isChecked():
            self._show_frame(self._clip().frame_start)
            self._clock_frame = self._frame
            self._clock.restart()
        # A run's wedges are compared, not played one after another.
        elif not self._runs and self._current_index + 1 < len(self._clips):
            self._load_clip(self._current_index + 1)
        else:
            self._show_frame(self._clip().frame_end)
            self._pause()

    def _go_to_frame(self, frame: int) -> None:
        # Pause first: playback would drag the frame back within one tick.
        # `_show_frame` clamps, so the clip's own ends are the limits.
        self._pause()
        self._show_frame(frame)

    def _go_to_clip(self, index: int) -> None:
        """Also the clip list's selection handler. Out-of-range does nothing —
        wrapping would make the two arrows indistinguishable on the last clip."""
        if 0 <= index < len(self._clips) and index != self._current_index:
            self._load_clip(index, keep_frame=self._clips[index] in self._run())

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    def _run(self) -> list[PreviewClip]:
        """The clips still in the window of the current clip's run."""
        clip = self._clip()
        run = next((run for run in self._runs if clip in run), [clip])
        return [other for other in run if other in self._clips]

    def _page(self) -> list[PreviewClip]:
        run = self._run()
        first = run.index(self._clip()) // grid.PAGE * grid.PAGE
        return run[first : first + grid.PAGE]

    def _refresh_view(self) -> None:
        """Shows the grid or the current clip alone, at the current frame."""
        self._shown = self._page() if self._mode == "grid" else [self._clip()]
        self._transport.grid.setChecked(self._mode != "single")
        run = self._run()
        page = ""
        if self._mode == "grid" and len(run) > grid.PAGE:
            first = run.index(self._shown[0]) + 1
            page = f"{first}–{first + len(self._shown) - 1} of {len(run)}"
        self._transport.set_page(page)
        if self._mode == "grid":
            self._canvas.set_cells(
                [f"{n}  {clip.label}" for n, clip in enumerate(self._shown, 1)],
                cells=min(len(run), grid.PAGE),
                outlined=self._shown.index(self._clip()),
            )
        else:
            self._canvas.set_cells([], cells=1, outlined=None)
        self._show_frame(self._frame)

    def _on_grid(self) -> None:
        """The Grid button and G. While a wedge is entered, they return to
        the grid rather than leave it."""
        self._mode = "single" if self._mode == "grid" else "grid"
        self._refresh_view()

    def _enter(self, cell: int) -> None:
        page = self._page()
        if cell >= len(page):
            return
        self._mode = "entered"
        index = self._clips.index(page[cell])
        if index == self._current_index:
            self._refresh_view()
        else:
            self._go_to_clip(index)

    def _on_canvas_clicked(self, cell: int) -> None:
        if self._mode == "grid":
            if cell >= 0:
                self._enter(cell)
        elif self._mode == "entered":
            self._mode = "grid"
            self._refresh_view()

    def _turn_page(self, step: int) -> None:
        """Pages move the current clip to the first wedge of the page."""
        run = self._run()
        first = (run.index(self._clip()) // grid.PAGE + step) * grid.PAGE
        if 0 <= first < len(run):
            self._go_to_clip(self._clips.index(run[first]))

    # ------------------------------------------------------------------
    # Scrubbing
    # ------------------------------------------------------------------

    def _on_scrub_pressed(self) -> None:
        self._was_playing_before_scrub = self._playing
        self._pause()
        self._show_frame(self._transport.scrub.value())

    def _on_scrub_moved(self, frame: int) -> None:
        self._show_frame(frame)

    def _on_scrub_released(self) -> None:
        if self._was_playing_before_scrub:
            self._play()

    # ------------------------------------------------------------------
    # Confirm state
    # ------------------------------------------------------------------

    def _on_clip_delivered(self, index: int) -> None:
        if index != self._current_index:
            return
        following = self._confirm.next_unconfirmed(index)
        if following is not None:
            self._load_clip(following)

    def _refresh_sidebar(self) -> None:
        self._sidebar.refresh(self._confirm.statuses())

    def _confirm_current(self) -> None:
        """The keyboard's route to Confirm — the button never takes focus.
        Inert on a panel with nothing to deliver, exactly as the button is."""
        self._confirm.panels[self._current_index].request_confirm()

    # ------------------------------------------------------------------
    # Window events and failure reporting
    # ------------------------------------------------------------------

    def _warn_missing_frames(self, clips: list[PreviewClip], frame: int) -> None:
        """Explain unreadable frames once per clip, then go quiet — a modal
        for every frame of a broken clip would be unusable."""
        self._missing_frames_warned.update(clips)
        self._pause()
        labels = ", ".join(clip.label for clip in clips)
        QMessageBox.warning(
            self,
            "Playblast Viewer",
            f"Could not load the preview frames for {labels}.\n\n"
            f"Missing: {clips[0].frame_path(frame, self._views.view)}\n\n"
            f"{self._missing_frames}",
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Transport, clip and grid navigation."""
        if isinstance(self.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return
        # Keypad arrows carry KeypadModifier and mean the same thing; any other
        # modifier makes the chord somebody else's.
        if event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            super().keyPressEvent(event)
            return
        clip = self._clip()
        key = event.key()
        if key == Qt.Key.Key_Space:
            # Held down, Space would strobe at the key-repeat rate.
            if not event.isAutoRepeat():
                self._toggle_playback()
        elif key == Qt.Key.Key_Left:
            self._go_to_frame(self._frame - 1)
        elif key == Qt.Key.Key_Right:
            self._go_to_frame(self._frame + 1)
        elif key == Qt.Key.Key_Home:
            self._go_to_frame(clip.frame_start)
        elif key == Qt.Key.Key_End:
            self._go_to_frame(clip.frame_end)
        elif key == Qt.Key.Key_Up:
            self._go_to_clip(self._current_index - 1)
        elif key == Qt.Key.Key_Down:
            self._go_to_clip(self._current_index + 1)
        elif key == Qt.Key.Key_R and self._views.isEnabled():
            self._views.cycle()
        elif self._runs and key == Qt.Key.Key_G:
            self._on_grid()
        elif self._mode == "entered" and key == Qt.Key.Key_Escape:
            self._on_grid()
        elif self._mode != "single" and key in _CELL_KEYS:
            self._enter(_CELL_KEYS.index(key))
        elif self._mode == "grid" and key == Qt.Key.Key_PageUp:
            self._turn_page(-1)
        elif self._mode == "grid" and key == Qt.Key.Key_PageDown:
            self._turn_page(1)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm.ask_to_close():
            event.accept()
        else:
            event.ignore()


__all__ = ["ViewerWindow"]
