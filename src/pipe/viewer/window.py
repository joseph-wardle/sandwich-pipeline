"""The viewer window: frame playback, transport controls, clip list."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QSize, Qt, QTimer
from PySide6.QtGui import (
    QFontDatabase,
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.preview_spec import PreviewClip, PreviewSpec

_SIDEBAR_WIDTH = 200
_TRANSPORT_HEIGHT = 76


class _TimelineSlider(QSlider):
    """Scrub bar."""

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(
                QStyle.sliderValueFromPosition(
                    self.minimum(),
                    self.maximum(),
                    int(event.position().x()),
                    self.width(),
                )
            )
        # The handle now sits under the cursor, so the default handler starts
        # a normal drag from it.
        super().mousePressEvent(event)


class ViewerWindow(QMainWindow):
    _spec: PreviewSpec
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
    _loop_checkbox: QCheckBox
    _scrub: _TimelineSlider
    _frame_label: QLabel

    def __init__(self, spec: PreviewSpec) -> None:
        super().__init__()
        self._spec = spec
        self._current_index = 0
        self._frame = spec.clips[0].frame_start
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
        self._ticker.setInterval(max(1, round(1000 / self._fps())))
        self._ticker.timeout.connect(self._on_tick)

        self._build_ui()
        self._build_shortcuts()
        self._size_to_video()

        self._load_clip(0)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        self._clip_list = QListWidget()
        self._clip_list.addItems([clip.label for clip in self._spec.clips])
        self._clip_list.setFixedWidth(_SIDEBAR_WIDTH)
        self._clip_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clip_list.currentRowChanged.connect(self._on_clip_selected)
        self._clip_list.setVisible(len(self._spec.clips) > 1)
        layout.addWidget(self._clip_list)

        self._canvas = QLabel()
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas.setStyleSheet("background-color: black;")
        # Ignored: the layout owns the label's size — otherwise each painted
        # pixmap would raise the minimum size and block shrinking the window.
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )

        player_column = QVBoxLayout()
        player_column.addWidget(self._canvas, stretch=1)
        player_column.addLayout(self._build_transport())
        layout.addLayout(player_column, stretch=1)

    def _build_transport(self) -> QVBoxLayout:
        transport = QVBoxLayout()

        self._scrub = _TimelineSlider()
        self._scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scrub.sliderMoved.connect(self._on_scrub_moved)
        self._scrub.sliderPressed.connect(self._on_scrub_pressed)
        self._scrub.sliderReleased.connect(self._on_scrub_released)
        transport.addWidget(self._scrub)

        row = QHBoxLayout()

        # Equal-stretch side sections keep the play cluster dead-center even
        # though the frame label and loop toggle differ in width.
        self._frame_label = QLabel("")
        self._frame_label.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        left = QHBoxLayout()
        left.addWidget(self._frame_label)
        left.addStretch(1)
        row.addLayout(left, stretch=1)

        row.addWidget(
            self._transport_button(
                QStyle.StandardPixmap.SP_MediaSeekBackward,
                "Step one frame back (Left)",
                lambda: self._step_frames(-1),
            )
        )
        self._play_button = self._transport_button(
            QStyle.StandardPixmap.SP_MediaPlay,
            "Play/pause (Space)",
            self._toggle_playback,
        )
        row.addWidget(self._play_button)
        row.addWidget(
            self._transport_button(
                QStyle.StandardPixmap.SP_MediaSeekForward,
                "Step one frame forward (Right)",
                lambda: self._step_frames(1),
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
        icon: QStyle.StandardPixmap,
        tooltip: str,
        on_click: Callable[[], None],
    ) -> QToolButton:
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(40, 32)
        button.setAutoRaise(True)
        # NoFocus keeps Space as the global play/pause shortcut instead of
        # "press the focused button".
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tooltip)
        button.clicked.connect(on_click)
        return button

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_playback)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._step_frames(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._step_frames(1))

    def _size_to_video(self) -> None:
        width, height = self._spec.resolution
        if len(self._spec.clips) > 1:
            width += _SIDEBAR_WIDTH
        height += _TRANSPORT_HEIGHT
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(width, screen.width() - 80), min(height, screen.height() - 80))

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _fps(self) -> int:
        return max(1, self._spec.fps)

    def _clip(self) -> PreviewClip:
        return self._spec.clips[self._current_index]

    def _load_clip(self, index: int) -> None:
        self._current_index = index
        clip = self._clip()
        self.setWindowTitle(f"Playblast Viewer — {clip.label}")
        if self._clip_list.currentRow() != index:
            self._clip_list.setCurrentRow(index)
        self._scrub.setRange(clip.frame_start, clip.frame_end)
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
        self._scrub.setValue(frame)
        self._frame_label.setText(
            f"frame {frame}  ({clip.frame_start}–{clip.frame_end})"
        )

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
        elif self._current_index + 1 < len(self._spec.clips):
            self._load_clip(self._current_index + 1)
        else:
            self._show_frame(self._clip().frame_end)
            self._pause()

    def _step_frames(self, delta: int) -> None:
        self._pause()
        self._show_frame(self._frame + delta)

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
    # Window events and failure reporting
    # ------------------------------------------------------------------

    def _on_clip_selected(self, row: int) -> None:
        if 0 <= row < len(self._spec.clips) and row != self._current_index:
            self._load_clip(row)

    def _set_play_icon(self, *, playing: bool) -> None:
        icon = (
            QStyle.StandardPixmap.SP_MediaPause
            if playing
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self._play_button.setIcon(self.style().standardIcon(icon))

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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._paint_canvas()


__all__ = ["ViewerWindow"]
