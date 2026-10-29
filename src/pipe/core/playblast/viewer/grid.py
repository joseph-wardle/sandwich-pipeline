"""The video canvas: one clip's frame, or a page of a run's wedges side by side."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from Qt.QtCore import QRect, Qt, Signal
from Qt.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from Qt.QtWidgets import QSizePolicy, QWidget

PAGE = 9
# Each grid as (up to this many cells, columns, rows).
_SHAPES = ((1, 1, 1), (2, 2, 1), (4, 2, 2), (PAGE, 3, 3))
_GAP = 2
_OUTLINE = 2
_CAPTION_PAD = 4
_CAPTION_BACKDROP = QColor(0, 0, 0, 160)

# One 720p PNG takes about 14 ms to decode, so a page of 9 read one after
# another misses 24 fps. QImage drops the GIL while it decodes, so a page
# read in parallel takes about one frame's time.
_DECODER = ThreadPoolExecutor(PAGE, thread_name_prefix="viewer-decode")


def load(paths: Sequence[Path]) -> list[QImage]:
    """The frames at `paths`, each null if it can't be read."""
    return list(_DECODER.map(lambda path: QImage(str(path)), paths))


def _shape(count: int) -> tuple[int, int]:
    return next((cols, rows) for most, cols, rows in _SHAPES if count <= most)


class Canvas(QWidget):
    """Paints the frames it is handed, scaled to fit their cells."""

    # The index of the cell clicked, or -1 for a click between cells.
    clicked = Signal(int)

    _images: list[QImage]
    _notes: Sequence[str]
    _captions: list[str]
    _outlined: int | None
    _cells: int

    def __init__(self) -> None:
        super().__init__()
        self._images = []
        self._notes = []
        self._captions = []
        self._outlined = None
        self._cells = 1
        # Ignored: the layout owns the canvas's size, so a big frame never
        # blocks shrinking the window.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_cells(
        self, captions: list[str], *, cells: int, outlined: int | None
    ) -> None:
        """`cells` sets the shape, so a run's last page keeps the size of
        the pages before it."""
        self._captions = captions
        self._cells = cells
        self._outlined = outlined

    def show_frames(self, images: list[QImage], notes: Sequence[str] = ()) -> None:
        """`notes` are shown in place of the frames that are null."""
        self._images = images
        self._notes = notes
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for index, image in enumerate(self._images):
            target = self._cell_rect(index)
            # A frame not on disk yet keeps its cell's caption and outline.
            if image.isNull():
                if index < len(self._notes):
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(
                        target, Qt.AlignmentFlag.AlignCenter, self._notes[index]
                    )
            else:
                target = _fit(image, target)
                painter.drawImage(target, image)
            if index < len(self._captions):
                _caption(painter, target, self._captions[index])
            if index == self._outlined:
                painter.setPen(QPen(self.palette().highlight().color(), _OUTLINE))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(target.adjusted(1, 1, -1, -1))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        position = event.pos()
        cells = range(len(self._images))
        hit = (i for i in cells if self._cell_rect(i).contains(position))
        self.clicked.emit(next(hit, -1))

    def _cell_rect(self, index: int) -> QRect:
        cols, rows = _shape(self._cells)
        width = (self.width() - _GAP * (cols - 1)) // cols
        height = (self.height() - _GAP * (rows - 1)) // rows
        column, row = index % cols, index // cols
        return QRect(column * (width + _GAP), row * (height + _GAP), width, height)


def _fit(image: QImage, cell: QRect) -> QRect:
    """The largest rect of the image's aspect centred in the cell."""
    size = image.size().scaled(cell.size(), Qt.AspectRatioMode.KeepAspectRatio)
    target = QRect(cell.topLeft(), size)
    target.moveCenter(cell.center())
    return target


def _caption(painter: QPainter, target: QRect, text: str) -> None:
    metrics = painter.fontMetrics()
    width = target.width() - _CAPTION_PAD * 2
    text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)
    box = QRect(
        target.left(),
        target.top(),
        metrics.horizontalAdvance(text) + _CAPTION_PAD * 2,
        metrics.height() + _CAPTION_PAD * 2,
    )
    painter.fillRect(box, _CAPTION_BACKDROP)
    painter.setPen(Qt.GlobalColor.white)
    painter.drawText(
        box.adjusted(_CAPTION_PAD, _CAPTION_PAD, 0, 0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        text,
    )


__all__ = ["PAGE", "Canvas", "load"]
