"""The clip sidebar: a filmstrip of thumbnails, labels and confirm status."""

from __future__ import annotations

from typing import cast

from Qt.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from Qt.QtGui import QColor, QPainter, QPalette, QPixmap
from Qt.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer import style
from pipe.core.playblast.viewer.confirm_column import UNCONFIRMED
from pipe.core.playblast.viewer.confirm_panel import ConfirmPanel, PanelStatus
from pipe.core.ui import FAIL, OK

SIDEBAR_WIDTH = 200
# Filmstrip thumbnail width; row height follows from the clip aspect ratio.
THUMB_W = 80

# Custom item-data roles are plain ints past UserRole (the Qt convention —
# PySide6's ItemDataRole enum cannot even hold UserRole + 1), but the stubs
# type every `role` parameter as the enum, so cast once here.
THUMB_ROLE = cast(Qt.ItemDataRole, int(Qt.ItemDataRole.UserRole))
STATUS_ROLE = cast(Qt.ItemDataRole, int(Qt.ItemDataRole.UserRole) + 1)

_STATUS_TEXT: dict[PanelStatus, str] = {
    PanelStatus.SKIPPED: "Skipped",
    PanelStatus.PENDING: "Pending",
    PanelStatus.RUNNING: "Running…",
    PanelStatus.CONFIRMED: "Confirmed",
    PanelStatus.FAILED: "Failed",
}


def _status_color(status: object, palette: QPalette) -> QColor:
    """Confirmed/failed carry semantic colors."""
    if status is PanelStatus.CONFIRMED:
        return QColor(OK)
    if status is PanelStatus.FAILED:
        return QColor(FAIL)
    if status is PanelStatus.RUNNING:
        return palette.highlight().color()
    return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)


def thumbnail(clip: PreviewClip, size: QSize) -> QPixmap:
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


class ClipDelegate(QStyledItemDelegate):
    """One filmstrip row: thumbnail, clip label, and any confirm status."""

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
        self._paint_thumbnail(painter, option, thumb_rect, index)
        self._paint_text(painter, option, thumb_rect, index, selected)
        painter.restore()

    def _paint_background(
        self, painter: QPainter, option: QStyleOptionViewItem, selected: bool
    ) -> None:
        if selected:
            painter.fillRect(option.rect, option.palette.highlight().color())
        elif option.state & QStyle.StateFlag.State_MouseOver:
            hover = option.palette.highlight().color()
            hover.setAlpha(40)
            painter.fillRect(option.rect, hover)

    def _paint_thumbnail(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        thumb_rect: QRect,
        index: QModelIndex,
    ) -> None:
        thumb = index.data(THUMB_ROLE)
        if isinstance(thumb, QPixmap) and not thumb.isNull():
            # Centre in the box so aspect-rounding slack doesn't shift the image.
            target = thumb.rect()
            target.moveCenter(thumb_rect.center())
            painter.drawPixmap(target.topLeft(), thumb)
        else:
            painter.fillRect(thumb_rect, option.palette.color(QPalette.ColorRole.Base))
        painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
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
        # the thumbnail. A clip with no status centres its label alone.
        status = index.data(STATUS_ROLE)
        lines = 1 if status is None else 2
        metrics = option.fontMetrics
        line_h = metrics.height()
        block_top = option.rect.y() + (option.rect.height() - line_h * lines) // 2
        text_x = thumb_rect.right() + 1 + self._GAP

        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        label = metrics.elidedText(
            label, Qt.TextElideMode.ElideRight, option.rect.right() - self._PAD - text_x
        )
        role = (
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        painter.setPen(option.palette.color(role))
        painter.drawText(text_x, block_top + metrics.ascent(), label)
        if status is None:
            return

        color = _status_color(status, option.palette)
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
        painter.setPen(
            option.palette.color(QPalette.ColorRole.HighlightedText)
            if selected
            else color
        )
        painter.drawText(status_x, baseline, _STATUS_TEXT.get(status, ""))


class Sidebar(QWidget):
    """The clip list, with Confirm remaining under it."""

    clips: QListWidget
    confirm_remaining: QPushButton
    _statuses: bool

    def __init__(
        self,
        clips: list[PreviewClip],
        resolution: tuple[int, int],
        *,
        statuses: bool,
    ) -> None:
        """Clips without `statuses` are acted on, not delivered, and their
        labels are long."""
        super().__init__()
        self._statuses = statuses
        self.clips = QListWidget()
        self.clips.setFixedWidth(SIDEBAR_WIDTH)
        self.clips.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clips.setMouseTracking(True)  # so rows repaint on hover
        self.clips.setToolTip("Up and Down move between clips.")
        width, height = resolution
        thumb_h = round(THUMB_W * height / width) if width else THUMB_W
        self.clips.setItemDelegate(ClipDelegate(THUMB_W, thumb_h))
        thumb_size = QSize(THUMB_W, thumb_h)
        for clip in clips:
            item = QListWidgetItem(clip.label)
            item.setData(THUMB_ROLE, thumbnail(clip, thumb_size))
            if statuses:
                item.setData(STATUS_ROLE, PanelStatus.PENDING)
            else:
                item.setToolTip(clip.label)
            self.clips.addItem(item)

        header = QLabel("Clips")
        header.setStyleSheet("font-weight: 600; padding: 0 2px;")
        self.confirm_remaining = QPushButton("Confirm remaining")
        self.confirm_remaining.setToolTip(
            "Deliver every clip that still has work to do — including retrying "
            "failures — using the choices already made on each one."
        )
        self.confirm_remaining.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sidebar = QVBoxLayout(self)
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(style.PAD_S)
        sidebar.addWidget(header)
        sidebar.addWidget(self.clips)
        sidebar.addWidget(self.confirm_remaining)
        self.setVisible(len(clips) > 1)

    def select(self, index: int) -> None:
        if self.clips.currentRow() != index:
            self.clips.setCurrentRow(index)

    def remove(self, index: int) -> None:
        # Taking the row would select its neighbour and load a stale index.
        self.clips.blockSignals(True)
        self.clips.takeItem(index)
        self.clips.blockSignals(False)

    def refresh(self, panels: list[ConfirmPanel]) -> None:
        if self._statuses:
            for index, panel in enumerate(panels):
                self.clips.item(index).setData(STATUS_ROLE, panel.status)
        self.clips.viewport().update()
        confirmable = [panel for panel in panels if panel.is_confirmable]
        self.confirm_remaining.setVisible(len(confirmable) > 1)
        self.confirm_remaining.setEnabled(
            any(panel.status in UNCONFIRMED for panel in confirmable)
        )


__all__ = [
    "SIDEBAR_WIDTH",
    "STATUS_ROLE",
    "THUMB_ROLE",
    "THUMB_W",
    "ClipDelegate",
    "Sidebar",
    "thumbnail",
]
