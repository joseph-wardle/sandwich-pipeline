"""The viewer window: frame playback, transport controls, clip list."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QElapsedTimer,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.preview_spec import PreviewClip, PreviewSpec
from pipe.viewer import icons, style
from pipe.viewer.confirm_panel import ConfirmPanel, PanelStatus

_SIDEBAR_WIDTH = 200
_TRANSPORT_HEIGHT = 112
_CONFIRM_PANEL_WIDTH = 300

_STEP_BUTTON = 34
_STEP_ICON = 16
_PLAY_ICON = 26

# Taller than a bare slider to leave room above the handle for the frame
# readout that tracks it.
_SCRUB_HEIGHT = 48

# Filmstrip thumbnail width; row height follows from the clip aspect ratio.
_THUMB_W = 80
_THUMB_ROLE = int(Qt.ItemDataRole.UserRole)
_STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 1

_STATUS_TEXT: dict[PanelStatus, str] = {
    PanelStatus.PENDING: "Pending",
    PanelStatus.RUNNING: "Running…",
    PanelStatus.CONFIRMED: "Confirmed",
    PanelStatus.FAILED: "Failed",
}
_STATUS_COLORS: dict[PanelStatus, QColor] = {
    PanelStatus.PENDING: QColor(style.MUTED),
    PanelStatus.RUNNING: QColor(style.ACCENT),
    PanelStatus.CONFIRMED: QColor(style.OK),
    PanelStatus.FAILED: QColor(style.FAIL),
}
_UNCONFIRMED = (PanelStatus.PENDING, PanelStatus.RUNNING, PanelStatus.FAILED)


class _TimelineSlider(QSlider):
    """Scrub bar: frame ticks on the unplayed track, plus a current-frame
    readout that tracks the handle."""

    # Nice steps to fall back through when per-frame ticks would smear; the
    # first whose spacing clears _MIN_TICK_GAP wins.
    _TICK_STEPS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000)
    _MIN_TICK_GAP = 5.0
    _TICK_HALF = 3  # px above/below the groove centre

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setFixedHeight(_SCRUB_HEIGHT)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

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

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        control = QStyle.ComplexControl.CC_Slider
        groove = self.style().subControlRect(
            control, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            control, option, QStyle.SubControl.SC_SliderHandle, self
        )
        painter = QPainter(self)
        self._paint_ticks(painter, groove, handle)
        self._paint_readout(painter, handle)
        painter.end()

    def _paint_ticks(self, painter: QPainter, groove: QRect, handle: QRect) -> None:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return
        # Values map onto the track the handle centre can actually reach.
        available = groove.width() - handle.width()
        if available <= 0:
            return
        px_per_frame = available / span
        step = next(
            (s for s in self._TICK_STEPS if s * px_per_frame >= self._MIN_TICK_GAP),
            0,
        )
        if step == 0:
            return  # even the coarsest step would smear — leave the bar clean

        left = groove.x() + handle.width() / 2
        played_to = (
            left + (self.value() - self.minimum()) * px_per_frame + handle.width() / 2
        )

        # Snap every edge to the physical pixel grid. A 1px logical tick spans a
        # fractional number of device pixels under display scaling, so without
        # snapping each tick rounds to one or two pixels by sub-pixel position.
        dpr = self.devicePixelRatioF()

        def snap(value: float) -> float:
            return round(value * dpr) / dpr

        tick_w = max(1, round(dpr)) / dpr
        top = snap(groove.center().y() - self._TICK_HALF)
        height = snap(self._TICK_HALF * 2)
        frame = self.minimum()
        while frame <= self.maximum():
            x = left + (frame - self.minimum()) * px_per_frame
            frame += step
            if x <= played_to:
                continue
            painter.fillRect(
                QRectF(snap(x), top, tick_w, height), QColor(style.BORDER_STRONG)
            )

    def _paint_readout(self, painter: QPainter, handle: QRect) -> None:
        # The slider value is the current frame; draw it above the handle so
        # the number sits where the playhead is, clamped inside the ends.
        text = str(self.value())
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text)
        x = min(max(handle.center().x() - text_w / 2, 0), self.width() - text_w)
        painter.setPen(QColor(style.TEXT_BRIGHT))
        painter.drawText(int(x), metrics.ascent(), text)


class _ClipDelegate(QStyledItemDelegate):
    """One filmstrip row: thumbnail, clip label, and confirm status."""

    _PAD = 8
    _GAP = 8
    _DOT_R = 3

    def __init__(self, thumb_w: int, thumb_h: int) -> None:
        super().__init__()
        self._thumb_w = thumb_w
        self._thumb_h = thumb_h

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(0, self._thumb_h + self._PAD * 2)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        thumb_rect = QRect(
            option.rect.x() + self._PAD,
            option.rect.y() + self._PAD,
            self._thumb_w,
            self._thumb_h,
        )
        self._paint_background(painter, option, selected)
        self._paint_thumbnail(painter, thumb_rect, index)
        self._paint_text(painter, option, thumb_rect, index, selected)
        painter.restore()

    def _paint_background(
        self, painter: QPainter, option: QStyleOptionViewItem, selected: bool
    ) -> None:
        if selected:
            painter.fillRect(option.rect, QColor(style.ACCENT))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(style.RAISED))

    def _paint_thumbnail(
        self, painter: QPainter, thumb_rect: QRect, index: QModelIndex
    ) -> None:
        thumb = index.data(_THUMB_ROLE)
        if isinstance(thumb, QPixmap) and not thumb.isNull():
            # Centre in the box so aspect-rounding slack doesn't shift the image.
            target = thumb.rect()
            target.moveCenter(thumb_rect.center())
            painter.drawPixmap(target.topLeft(), thumb)
        else:
            painter.fillRect(thumb_rect, QColor(style.BASE))
        painter.setPen(QColor(style.BORDER))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(thumb_rect)

    def _paint_text(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        thumb_rect: QRect,
        index: QModelIndex,
        selected: bool,
    ) -> None:
        # Label and status word stack as two lines, vertically centred against
        # the thumbnail.
        metrics = option.fontMetrics
        line_h = metrics.height()
        block_top = option.rect.y() + (option.rect.height() - line_h * 2) // 2
        text_x = thumb_rect.right() + 1 + self._GAP

        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        label = metrics.elidedText(
            label, Qt.TextElideMode.ElideRight, option.rect.right() - self._PAD - text_x
        )
        painter.setPen(QColor(style.TEXT_BRIGHT if selected else style.TEXT))
        painter.drawText(text_x, block_top + metrics.ascent(), label)

        status = index.data(_STATUS_ROLE)
        color = _STATUS_COLORS.get(status, QColor(style.MUTED))
        baseline = block_top + line_h + metrics.ascent()
        dot_cx = text_x + self._DOT_R
        dot_cy = baseline - metrics.ascent() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(
            QRectF(
                dot_cx - self._DOT_R,
                dot_cy - self._DOT_R,
                self._DOT_R * 2,
                self._DOT_R * 2,
            )
        )
        status_x = dot_cx + self._DOT_R + 5
        painter.setPen(QColor(style.TEXT_BRIGHT) if selected else color)
        painter.drawText(status_x, baseline, _STATUS_TEXT.get(status, ""))


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
    _play_icon: QIcon
    _pause_icon: QIcon
    _loop_checkbox: QCheckBox
    _scrub: _TimelineSlider
    _start_label: QLabel
    _end_label: QLabel
    _panels: list[ConfirmPanel]
    _panel_stack: QStackedWidget
    _confirm_remaining_button: QPushButton

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
        layout.setContentsMargins(style.PAD_M, style.PAD_M, style.PAD_M, style.PAD_M)
        layout.setSpacing(style.PAD_M)

        self._clip_list = QListWidget()
        self._clip_list.setObjectName("clipList")
        self._clip_list.setFixedWidth(_SIDEBAR_WIDTH)
        self._clip_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clip_list.setMouseTracking(True)  # so rows repaint on hover
        width, height = self._spec.resolution
        thumb_h = round(_THUMB_W * height / width) if width else _THUMB_W
        self._clip_list.setItemDelegate(_ClipDelegate(_THUMB_W, thumb_h))
        thumb_size = QSize(_THUMB_W, thumb_h)
        for clip in self._spec.clips:
            item = QListWidgetItem(clip.label)
            item.setData(_THUMB_ROLE, self._thumbnail(clip, thumb_size))
            item.setData(_STATUS_ROLE, PanelStatus.PENDING)
            self._clip_list.addItem(item)
        self._clip_list.currentRowChanged.connect(self._on_clip_selected)

        header = QLabel("Clips")
        header.setStyleSheet(f"color: {style.MUTED}; font-weight: 600; padding: 0 2px;")
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(style.PAD_S)
        sidebar.addWidget(header)
        sidebar.addWidget(self._clip_list)
        self._confirm_remaining_button = QPushButton("Confirm remaining")
        self._confirm_remaining_button.clicked.connect(self._confirm_remaining)
        sidebar.addWidget(self._confirm_remaining_button)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setVisible(len(self._spec.clips) > 1)
        layout.addWidget(sidebar_widget)

        self._canvas = QLabel()
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas.setStyleSheet("background-color: black;")
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

    def _build_confirm_panels(self) -> None:
        self._panels = []
        self._panel_stack = QStackedWidget()
        self._panel_stack.setFixedWidth(_CONFIRM_PANEL_WIDTH)
        for index, clip in enumerate(self._spec.clips):
            panel = ConfirmPanel(clip, fps=self._spec.fps)
            panel.state_changed.connect(
                lambda clip_index=index: self._on_confirm_state_changed(clip_index)
            )
            self._panels.append(panel)
            self._panel_stack.addWidget(panel)
        # A spec with nothing to confirm (unmigrated DCC flows) keeps the
        # plain view-only window.
        self._panel_stack.setVisible(self._has_confirmables())
        self._refresh_sidebar()

    def _build_transport(self) -> QVBoxLayout:
        transport = QVBoxLayout()
        transport.setSpacing(style.GAP)

        self._scrub = _TimelineSlider()
        self._scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scrub.sliderMoved.connect(self._on_scrub_moved)
        self._scrub.sliderPressed.connect(self._on_scrub_pressed)
        self._scrub.sliderReleased.connect(self._on_scrub_released)

        # Muted start/end frames flank the bar so its ends read as the clip's
        # range; the current frame is drawn on the handle by the slider itself.
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._start_label = QLabel("")
        self._end_label = QLabel("")
        for label in (self._start_label, self._end_label):
            label.setFont(fixed)
            label.setStyleSheet(f"color: {style.MUTED};")
        scrub_row = QHBoxLayout()
        scrub_row.setSpacing(style.PAD_S)
        scrub_row.addWidget(self._start_label)
        scrub_row.addWidget(self._scrub, stretch=1)
        scrub_row.addWidget(self._end_label)
        transport.addLayout(scrub_row)

        # Drawn once and reused; the play button swaps between them on toggle.
        self._play_icon = icons.play(style.TEXT_BRIGHT, _PLAY_ICON)
        self._pause_icon = icons.pause(style.TEXT_BRIGHT, _PLAY_ICON)

        row = QHBoxLayout()
        row.setSpacing(style.GAP)

        # Equal-stretch side sections keep the play cluster dead-center; the
        # left one balances the loop toggle on the right.
        left = QHBoxLayout()
        left.addStretch(1)
        row.addLayout(left, stretch=1)

        row.addWidget(
            self._transport_button(
                icons.step_back(style.TEXT, _STEP_ICON),
                "Step one frame back (Left)",
                lambda: self._step_frames(-1),
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
                icons.step_forward(style.TEXT, _STEP_ICON),
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
        icon: QIcon,
        tooltip: str,
        on_click: Callable[[], None],
        *,
        primary: bool = False,
    ) -> QToolButton:
        button = QToolButton()
        button.setIcon(icon)
        if primary:
            # The round accent play button; QSS keys off this object name.
            button.setObjectName("transportPlay")
            button.setIconSize(QSize(_PLAY_ICON, _PLAY_ICON))
            button.setFixedSize(style.TRANSPORT_PLAY_SIZE, style.TRANSPORT_PLAY_SIZE)
        else:
            button.setAutoRaise(True)
            button.setIconSize(QSize(_STEP_ICON, _STEP_ICON))
            button.setFixedSize(_STEP_BUTTON, _STEP_BUTTON)
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
        if self._has_confirmables():
            width += _CONFIRM_PANEL_WIDTH
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
    # Confirm state
    # ------------------------------------------------------------------

    def _has_confirmables(self) -> bool:
        return any(panel.is_confirmable for panel in self._panels)

    def _on_confirm_state_changed(self, index: int) -> None:
        self._refresh_sidebar()
        panel = self._panels[index]
        if panel.status is PanelStatus.CONFIRMED and index == self._current_index:
            self._advance_to_next_pending()

    def _thumbnail(self, clip: PreviewClip, size: QSize) -> QPixmap:
        """A scaled preview frame for the filmstrip; empty if none loads (the
        delegate then draws a placeholder)."""
        mid = (clip.frame_start + clip.frame_end) // 2
        for frame in (mid, clip.frame_start):
            pixmap = QPixmap(str(clip.frame_path(frame)))
            if not pixmap.isNull():
                return pixmap.scaled(
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return QPixmap()

    def _refresh_sidebar(self) -> None:
        for index, panel in enumerate(self._panels):
            self._clip_list.item(index).setData(_STATUS_ROLE, panel.status)
        self._clip_list.viewport().update()
        confirmable = [panel for panel in self._panels if panel.is_confirmable]
        self._confirm_remaining_button.setVisible(len(confirmable) > 1)
        self._confirm_remaining_button.setEnabled(
            any(panel.status is PanelStatus.PENDING for panel in confirmable)
        )

    def _advance_to_next_pending(self) -> None:
        count = len(self._panels)
        for offset in range(1, count):
            candidate = (self._current_index + offset) % count
            if self._panels[candidate].status is PanelStatus.PENDING:
                self._load_clip(candidate)
                return

    def _confirm_remaining(self) -> None:
        for panel in self._panels:
            if panel.status is PanelStatus.PENDING:
                panel.request_confirm()

    # ------------------------------------------------------------------
    # Window events and failure reporting
    # ------------------------------------------------------------------

    def _on_clip_selected(self, row: int) -> None:
        if 0 <= row < len(self._spec.clips) and row != self._current_index:
            self._load_clip(row)

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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._paint_canvas()

    def closeEvent(self, event: QCloseEvent) -> None:
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
