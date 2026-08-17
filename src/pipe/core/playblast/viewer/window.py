"""The viewer window: frame playback, transport controls, clip list."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from Qt.QtCore import (
    QElapsedTimer,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
)
from Qt.QtGui import (
    QCloseEvent,
    QFontDatabase,
    QIcon,
    QImageReader,
    QKeyEvent,
    QKeySequence,
    QPalette,
    QPixmap,
    QResizeEvent,
)
from Qt.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.playblaster import DEFAULT_RESOLUTION
from pipe.core.playblast.viewer import filmstrip, icons, style
from pipe.core.playblast.viewer.confirm_panel import ConfirmPanel, PanelStatus
from pipe.core.playblast.viewer.playlists import ReviewPlaylists
from pipe.core.playblast.viewer.scrub import TimelineSlider

_SIDEBAR_WIDTH = 200
_TRANSPORT_HEIGHT = 112
_CONFIRM_PANEL_WIDTH = 300

_STEP_BUTTON = 34
_STEP_ICON = 16
_PLAY_ICON = 26

# Clips that still have something to deliver.
_UNCONFIRMED = (PanelStatus.PENDING, PanelStatus.FAILED)


def _frame_size(clip: PreviewClip) -> tuple[int, int] | None:
    """Pixel size of the clip's first frame, read from the PNG header alone,
    or None if the frame is unreadable."""
    size = QImageReader(str(clip.frame_path(clip.frame_start))).size()
    if size.width() > 0 and size.height() > 0:
        return size.width(), size.height()
    return None


class ViewerWindow(QMainWindow):
    _clips: list[PreviewClip]
    _resolution: tuple[int, int]
    _current_index: int
    _frame: int
    _pixmap: QPixmap
    _playing: bool
    _was_playing_before_scrub: bool
    _missing_frames_warned: set[int]
    _clock: QElapsedTimer
    _clock_frame: int
    _ticker: QTimer
    _canvas: QLabel
    _clip_list: QListWidget
    _play_button: QToolButton
    _play_icon: QIcon
    _pause_icon: QIcon
    _loop_checkbox: QCheckBox
    _scrub: TimelineSlider
    _start_label: QLabel
    _end_label: QLabel
    _confirm_pool: QThreadPool
    _playlists: ReviewPlaylists
    _panels: list[ConfirmPanel]
    _panel_stack: QStackedWidget
    _confirm_remaining_button: QPushButton

    def __init__(self, clips: list[PreviewClip], parent: QWidget | None) -> None:
        # The parent (the DCC main window) owns the viewer's lifetime; a
        # QMainWindow always carries Qt.Window, so it stays a real window
        # rather than embedding. DeleteOnClose frees it — nobody keeps a
        # reference.
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._clips = clips
        # Sizing only — an unreadable first frame (reported per clip on
        # playback) still deserves a sanely sized window.
        self._resolution = _frame_size(clips[0]) or DEFAULT_RESOLUTION
        self._current_index = 0
        self._frame = clips[0].frame_start
        self._pixmap = QPixmap()
        self._playing = False
        self._was_playing_before_scrub = False
        self._missing_frames_warned = set()

        # Playback clock: frames advance by wall-clock time, not tick count,
        # so a slow paint skips frames instead of slowing the clip down.
        self._clock = QElapsedTimer()
        self._clock_frame = self._frame
        self._ticker = QTimer(self)
        self._ticker.setTimerType(Qt.TimerType.PreciseTimer)
        self._ticker.timeout.connect(self._on_tick)

        # Confirm jobs run here, one at a time — parallel deliveries would
        # race on encode CPU and previs take numbering. The window owns the
        # pool (not the DCC-global one) so viewer work never mixes with the
        # host application's.
        self._confirm_pool = QThreadPool(self)
        self._confirm_pool.setMaxThreadCount(1)
        self._playlists = ReviewPlaylists(self)

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

        self._clip_list = QListWidget()
        self._clip_list.setFixedWidth(_SIDEBAR_WIDTH)
        self._clip_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clip_list.setMouseTracking(True)  # so rows repaint on hover
        self._clip_list.setToolTip("Up and Down move between clips.")
        width, height = self._resolution
        thumb_w = filmstrip.THUMB_W
        thumb_h = round(thumb_w * height / width) if width else thumb_w
        self._clip_list.setItemDelegate(filmstrip.ClipDelegate(thumb_w, thumb_h))
        thumb_size = QSize(thumb_w, thumb_h)
        for clip in self._clips:
            item = QListWidgetItem(clip.label)
            item.setData(filmstrip.THUMB_ROLE, filmstrip.thumbnail(clip, thumb_size))
            item.setData(filmstrip.STATUS_ROLE, PanelStatus.PENDING)
            self._clip_list.addItem(item)
        self._clip_list.currentRowChanged.connect(self._go_to_clip)

        header = QLabel("Clips")
        header.setStyleSheet("font-weight: 600; padding: 0 2px;")
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(style.PAD_S)
        sidebar.addWidget(header)
        sidebar.addWidget(self._clip_list)
        self._confirm_remaining_button = QPushButton("Confirm remaining")
        self._confirm_remaining_button.setToolTip(
            "Deliver every clip that still has work to do — including retrying "
            "failures — using the choices already made on each one."
        )
        self._confirm_remaining_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._confirm_remaining_button.clicked.connect(self._confirm_remaining)
        sidebar.addWidget(self._confirm_remaining_button)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setVisible(len(self._clips) > 1)
        layout.addWidget(sidebar_widget)

        self._canvas = QLabel()
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas.setStyleSheet("background-color: black;")
        # The video holds focus, so the transport keys work the moment the
        # window opens and clicking the video takes them back from a text
        # field. Left to itself Qt would focus the first field instead.
        self._canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._canvas.setFocus()
        # Ignored: the layout owns the label's size — otherwise each painted
        # pixmap would raise the minimum size and block shrinking the window.
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )

        player_column = QVBoxLayout()
        player_column.setSpacing(style.GAP)
        player_column.addWidget(self._canvas, stretch=1)
        player_column.addLayout(self._build_transport())
        layout.addLayout(player_column, stretch=1)

        self._build_confirm_panels()
        layout.addWidget(self._panel_stack)

        # Confirm is a shortcut rather than a keyPressEvent branch: a focused
        # QLineEdit ignores Return, so the window would confirm *and* the field
        # would fire its own returnPressed. A shortcut runs before either.
        # Key_Enter is the keypad's, and needs its own sequence to match.
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(sequence), self, self._confirm_current)

    def _build_confirm_panels(self) -> None:
        self._panels = []
        self._panel_stack = QStackedWidget()
        self._panel_stack.setFixedWidth(_CONFIRM_PANEL_WIDTH)
        for index, clip in enumerate(self._clips):
            panel = ConfirmPanel(clip, self._confirm_pool, self._playlists)
            panel.state_changed.connect(self._refresh_sidebar)
            panel.delivered.connect(
                lambda clip_index=index: self._on_clip_delivered(clip_index)
            )
            self._panels.append(panel)
            self._panel_stack.addWidget(panel)
        # A batch with nothing to confirm (unmigrated DCC flows) keeps the
        # plain view-only window.
        self._panel_stack.setVisible(self._has_confirmables())
        self._refresh_sidebar()

    def _build_transport(self) -> QVBoxLayout:
        transport = QVBoxLayout()
        transport.setSpacing(style.GAP)

        self._scrub = TimelineSlider()
        self._scrub.setToolTip("Home and End jump to the first and last frame.")
        self._scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scrub.sliderMoved.connect(self._on_scrub_moved)
        self._scrub.sliderPressed.connect(self._on_scrub_pressed)
        self._scrub.sliderReleased.connect(self._on_scrub_released)

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
        scrub_row.addWidget(self._scrub, stretch=1)
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
        left.addStretch(1)
        row.addLayout(left, stretch=1)

        row.addWidget(
            self._transport_button(
                icons.step_back(icon_color, _STEP_ICON),
                "Step one frame back (Left)",
                lambda: self._go_to_frame(self._frame - 1),
            )
        )
        self._play_button = self._transport_button(
            self._play_icon,
            "Play/pause (Space)",
            self._toggle_playback,
            primary=True,
        )
        row.addWidget(self._play_button)
        row.addWidget(
            self._transport_button(
                icons.step_forward(icon_color, _STEP_ICON),
                "Step one frame forward (Right)",
                lambda: self._go_to_frame(self._frame + 1),
            )
        )

        self._loop_checkbox = QCheckBox("Loop")
        self._loop_checkbox.setChecked(True)
        self._loop_checkbox.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right = QHBoxLayout()
        right.addStretch(1)
        right.addWidget(self._loop_checkbox)
        row.addLayout(right, stretch=1)

        transport.addLayout(row)
        return transport

    def _transport_button(
        self,
        icon: QIcon,
        tooltip: str,
        on_click: Callable[[], None],
        *,
        primary: bool = False,
    ) -> QToolButton:
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
        button.clicked.connect(on_click)
        return button

    def _size_to_video(self) -> None:
        width, height = self._resolution
        if len(self._clips) > 1:
            width += _SIDEBAR_WIDTH
        if self._has_confirmables():
            width += _CONFIRM_PANEL_WIDTH
        height += _TRANSPORT_HEIGHT
        screen = self.screen().availableGeometry()
        self.resize(min(width, screen.width() - 80), min(height, screen.height() - 80))

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _fps(self) -> int:
        return max(1, self._clip().fps)

    def _clip(self) -> PreviewClip:
        return self._clips[self._current_index]

    def _load_clip(self, index: int) -> None:
        self._current_index = index
        clip = self._clip()
        self._ticker.setInterval(max(1, round(1000 / self._fps())))
        self.setWindowTitle(f"Playblast Viewer — {clip.label}")
        if self._clip_list.currentRow() != index:
            self._clip_list.setCurrentRow(index)
        self._panel_stack.setCurrentIndex(index)
        self._scrub.setRange(clip.frame_start, clip.frame_end)
        self._start_label.setText(str(clip.frame_start))
        self._end_label.setText(str(clip.frame_end))
        self._show_frame(clip.frame_start)
        self._play()

    def _show_frame(self, frame: int) -> None:
        clip = self._clip()
        frame = min(max(frame, clip.frame_start), clip.frame_end)
        pixmap = QPixmap(str(clip.frame_path(frame)))
        if pixmap.isNull():
            self._warn_missing_frames(clip.frame_path(frame))
            return
        self._frame = frame
        self._pixmap = pixmap
        self._paint_canvas()
        # The slider repaints its handle readout from this value.
        self._scrub.setValue(frame)

    def _paint_canvas(self) -> None:
        if self._pixmap.isNull():
            return
        self._canvas.setPixmap(
            self._pixmap.scaled(
                self._canvas.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

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
        self._set_play_icon(playing=True)

    def _pause(self) -> None:
        self._playing = False
        self._ticker.stop()
        self._set_play_icon(playing=False)

    def _on_tick(self) -> None:
        clip = self._clip()
        frame = self._clock_frame + self._clock.elapsed() * self._fps() // 1000
        if frame > clip.frame_end:
            self._on_clip_end()
        elif frame != self._frame:
            self._show_frame(frame)

    def _on_clip_end(self) -> None:
        if self._loop_checkbox.isChecked():
            self._show_frame(self._clip().frame_start)
            self._clock_frame = self._frame
            self._clock.restart()
        elif self._current_index + 1 < len(self._clips):
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
            self._load_clip(index)

    # ------------------------------------------------------------------
    # Scrubbing
    # ------------------------------------------------------------------

    def _on_scrub_pressed(self) -> None:
        self._was_playing_before_scrub = self._playing
        self._pause()
        self._show_frame(self._scrub.value())

    def _on_scrub_moved(self, frame: int) -> None:
        self._show_frame(frame)

    def _on_scrub_released(self) -> None:
        if self._was_playing_before_scrub:
            self._play()

    # ------------------------------------------------------------------
    # Confirm state
    # ------------------------------------------------------------------

    def _has_confirmables(self) -> bool:
        return any(panel.is_confirmable for panel in self._panels)

    def _on_clip_delivered(self, index: int) -> None:
        if index == self._current_index:
            self._advance_to_next_unconfirmed()

    def _refresh_sidebar(self) -> None:
        for index, panel in enumerate(self._panels):
            self._clip_list.item(index).setData(filmstrip.STATUS_ROLE, panel.status)
        self._clip_list.viewport().update()
        confirmable = [panel for panel in self._panels if panel.is_confirmable]
        self._confirm_remaining_button.setVisible(len(confirmable) > 1)
        self._confirm_remaining_button.setEnabled(
            any(panel.status in _UNCONFIRMED for panel in confirmable)
        )

    def _advance_to_next_unconfirmed(self) -> None:
        count = len(self._panels)
        for offset in range(1, count):
            candidate = (self._current_index + offset) % count
            if self._panels[candidate].status in _UNCONFIRMED:
                self._load_clip(candidate)
                return

    def _confirm_current(self) -> None:
        """The keyboard's route to Confirm — the button never takes focus.
        Inert on a panel with nothing to deliver, exactly as the button is."""
        self._panels[self._current_index].request_confirm()

    def _confirm_remaining(self) -> None:
        for panel in self._panels:
            if panel.status in _UNCONFIRMED:
                panel.request_confirm()

    # ------------------------------------------------------------------
    # Window events and failure reporting
    # ------------------------------------------------------------------

    def _set_play_icon(self, *, playing: bool) -> None:
        self._play_button.setIcon(self._pause_icon if playing else self._play_icon)

    def _warn_missing_frames(self, path: Path) -> None:
        """Explain an unreadable frame once per clip, then go quiet — a
        modal for every frame of a broken clip would be unusable."""
        if self._current_index in self._missing_frames_warned:
            return
        self._missing_frames_warned.add(self._current_index)
        self._pause()
        QMessageBox.warning(
            self,
            "Playblast Viewer",
            f"Could not load the preview frames for {self._clip().label}.\n\n"
            f"Missing: {path}\n\n"
            "The preview's temp files may have been cleaned up — close the "
            "viewer and playblast again.",
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Transport and clip navigation."""
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
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._paint_canvas()

    def closeEvent(self, event: QCloseEvent) -> None:
        # A running delivery is already writing files and pipeline records;
        # closing mid-flight would abandon it half-done, so it can't be
        # discarded — only waited out.
        if any(panel.status is PanelStatus.RUNNING for panel in self._panels):
            QMessageBox.information(
                self,
                "Playblast Viewer",
                "A delivery is still running. Wait for it to finish, then close.",
            )
            event.ignore()
            return
        unconfirmed = [panel for panel in self._panels if panel.status in _UNCONFIRMED]
        if not unconfirmed:
            event.accept()
            return
        count = len(unconfirmed)
        message = (
            "1 preview hasn't been confirmed and will be discarded."
            if count == 1
            else f"{count} previews haven't been confirmed and will be discarded."
        )
        reply = QMessageBox.question(
            self,
            "Playblast Viewer",
            f"{message}\nClose anyway?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Discard:
            event.accept()
        else:
            event.ignore()


__all__ = ["ViewerWindow"]
