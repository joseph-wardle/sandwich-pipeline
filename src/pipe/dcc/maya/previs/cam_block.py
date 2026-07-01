"""Single camera block. Drag a block onto a Maya viewport to look through that
camera; drag an alternate onto its column's primary to promote; double-click an
alt for the same; right-click for menu; drag the right-edge handle on primaries
to resize the shot.

The resize handle is a dedicated child widget rather than edge-detection inside
the QFrame so mouse events land unambiguously.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from Qt import QtCore, QtGui
from Qt.QtCore import QMimeData
from Qt.QtGui import QCursor, QDrag
from Qt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import _qt, style

if TYPE_CHECKING:
    from .panel import PrevisPanel

BLOCK_HEIGHT = 32
_HANDLE_WIDTH = 10
_MIME_TYPE = "application/x-previs-camera"  # payload: f"{shot_id}|{namespace}"

# Hide the resize handle below this width — there's no room to grab it anyway,
# and the handle's stripes would crowd out the colored sliver.
_HANDLE_MIN_BLOCK_WIDTH = 28


class _ResizeHandle(QFrame):
    """Right-edge grabber for primary blocks. Owns its own mouse events."""

    def __init__(self, block: CamBlock, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = block
        self.setObjectName("resizeHandle")
        self.setFixedWidth(_HANDLE_WIDTH)
        self.setCursor(QCursor(_qt.SIZE_HOR))
        self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        self._drag_active = False
        self._drag_start_global_x = 0

    def enterEvent(self, event: QtCore.QEvent) -> None:
        if not self._drag_active:
            self.setStyleSheet(style.RESIZE_HANDLE_HOVER)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if not self._drag_active:
            self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != _qt.LEFT_BUTTON:
            super().mousePressEvent(event)
            return
        self._drag_active = True
        self._drag_start_global_x = event.globalPos().x()
        self.setStyleSheet(style.RESIZE_HANDLE_ACTIVE)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag_active:
            super().mouseMoveEvent(event)
            return
        self._block.preview_resize(event.globalPos().x() - self._drag_start_global_x)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag_active:
            super().mouseReleaseEvent(event)
            return
        self._drag_active = False
        self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        self._block.commit_resize(event.globalPos().x() - self._drag_start_global_x)
        event.accept()


class CamBlock(QFrame):
    def __init__(
        self,
        *,
        namespace: str,
        is_primary: bool,
        length_frames: int,
        start_frame: int,
        shot_id: str,
        controller: PrevisPanel,
        height: int = BLOCK_HEIGHT,
        px_per_frame: int = 4,
        truncated: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("camBlock")
        self._namespace = namespace
        self._is_primary = is_primary
        self._length_frames = length_frames
        self._start_frame = start_frame
        self._truncated = truncated
        # Taken verbatim from the timeline so drag math stays stable —
        # deriving px-per-frame from self.width() drifts because the block is
        # being live-resized during the drag.
        self._px_per_frame = max(1, px_per_frame)
        self._shot_id = shot_id
        self._controller = controller
        self._height_hint = height  # used by minimumSizeHint before geometry resolves
        self._handle: _ResizeHandle | None = None
        self._press_pos: QtCore.QPoint | None = None

        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Without WA_StyledBackground, Qt's native style ignores stylesheet
        # background-color and shows outline-only.
        self.setAttribute(_qt.STYLED_BACKGROUND, True)
        if is_primary:
            self.setStyleSheet(style.CAM_BLOCK_PRIMARY)
            self.setAcceptDrops(True)
        elif truncated:
            self.setStyleSheet(style.CAM_BLOCK_ALT_TRUNC)
        else:
            self.setStyleSheet(style.CAM_BLOCK_ALT)

        outer = QHBoxLayout(self)
        # Right margin 0 on primary: the resize handle owns that strip.
        # Left margin reduced on primary: the thick border-left already eats ~2px.
        outer.setContentsMargins(8 if is_primary else 10, 0, 0 if is_primary else 10, 0)
        outer.setSpacing(0)
        outer.addLayout(self._build_content(), 1)
        if is_primary:
            self._handle = _ResizeHandle(self, self)
            outer.addWidget(self._handle)

        self.setToolTip(self._tooltip_text())

    def minimumSizeHint(self) -> QtCore.QSize:
        # Both hints must override the default that cascades from child label
        # widths — QGridLayout columns with stretch=0 honor sizeHint (not just
        # min) when excess space is available, so a 200px sizeHint would still
        # inflate the column even with minimumSizeHint at 1.
        return QtCore.QSize(1, self._height_hint)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, self._height_hint)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_tier()

    def _build_content(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(2)

        # Decorative labels — `WA_TransparentForMouseEvents` lets presses fall
        # through to the QFrame so the block's own mousePressEvent fires.
        self._name_label = QLabel(self._namespace, self)
        self._name_label.setObjectName("name")
        self._name_label.setAttribute(_qt.TRANSPARENT_FOR_MOUSE, True)
        col.addWidget(self._name_label)

        col.addLayout(self._build_frame_row())
        return col

    def _build_frame_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._start_label = _frame_label("startFrame", str(self._start_frame), self)
        self._length_label = _frame_label(
            "lengthBadge", f"{self._length_frames}f", self
        )
        self._end_label = _frame_label("endFrame", self._end_label_text(), self)

        row.addWidget(self._start_label)
        row.addStretch(1)
        row.addWidget(self._length_label)
        row.addStretch(1)
        row.addWidget(self._end_label)
        return row

    def _end_frame(self) -> int:
        return self._start_frame + max(self._length_frames - 1, 0)

    def _end_label_text(self) -> str:
        text = str(self._end_frame())
        return f"{text} ›››" if self._truncated else text

    def _tooltip_text(self) -> str:
        suffix = "  (longer than primary)" if self._truncated else ""
        return (
            f"{self._namespace}\n"
            f"{self._start_frame} → {self._end_frame()}  ({self._length_frames}f)"
            f"{suffix}"
        )

    @property
    def length_frames(self) -> int:
        return self._length_frames

    def set_truncated(self, truncated: bool) -> None:
        """Swap alt-block stylesheet + end-label chevron when the truncated state flips."""
        if truncated == self._truncated:
            return
        self._truncated = truncated
        self.setStyleSheet(
            style.CAM_BLOCK_ALT_TRUNC if truncated else style.CAM_BLOCK_ALT
        )
        self._end_label.setText(self._end_label_text())
        self.setToolTip(self._tooltip_text())

    # --- tier-based progressive disclosure ---------------------------------

    def _apply_tier(self) -> None:
        """Show/hide labels and the resize handle based on current width.

        - ≥ COMPACT: full layout, name elided to fit.
        - NARROW–COMPACT: only the centered duration pill.
        - < NARROW: just a colored sliver; hover tooltip carries the info.
        """
        w = self.width()
        if self._handle is not None:
            self._handle.setVisible(w >= _HANDLE_MIN_BLOCK_WIDTH)
        full = w >= style.TIER_COMPACT
        compact = w >= style.TIER_NARROW
        self._name_label.setVisible(full)
        self._start_label.setVisible(full)
        self._end_label.setVisible(full)
        self._length_label.setVisible(compact)
        if full:
            self._elide_name()

    def _elide_name(self) -> None:
        outer_left = 8 if self._is_primary else 10
        outer_right = 0 if self._is_primary else 10
        handle_w = _HANDLE_WIDTH if (self._handle and self._handle.isVisible()) else 0
        available = self.width() - outer_left - outer_right - handle_w
        if available <= 0:
            return
        fm = self._name_label.fontMetrics()
        self._name_label.setText(
            fm.elidedText(self._namespace, _qt.ELIDE_RIGHT, available)
        )

    # --- resize hooks (called by _ResizeHandle) -----------------------------

    def preview_resize(self, delta_px: int) -> None:
        new_length = self._compute_new_length(delta_px)
        self._length_label.setText(f"{new_length}f")
        end = self._start_frame + max(new_length - 1, 0)
        self._end_label.setText(f"{end} ›››" if self._truncated else str(end))
        # Tooltip carries the in-flight numbers so a paused drag still reads true.
        self.setToolTip(
            f"{self._namespace}\n{self._start_frame} → {end}  ({new_length}f)"
        )
        self._controller.preview_resize_camera(
            self._shot_id, self._namespace, new_length
        )

    def commit_resize(self, delta_px: int) -> None:
        new_length = self._compute_new_length(delta_px)
        if new_length != self._length_frames:
            self._controller.resize_camera(self._shot_id, self._namespace, new_length)

    def _compute_new_length(self, delta_px: int) -> int:
        delta_frames = int(round(delta_px / self._px_per_frame))
        return max(1, self._length_frames + delta_frames)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == _qt.LEFT_BUTTON:
            self._press_pos = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_pos is not None and event.buttons() & _qt.LEFT_BUTTON:
            travel = (event.pos() - self._press_pos).manhattanLength()
            if travel >= QApplication.startDragDistance():
                self._start_drag()
                self._press_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == _qt.LEFT_BUTTON and not self._is_primary:
            self._controller.promote_to_primary(self._shot_id, self._namespace)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_MIME_TYPE, f"{self._shot_id}|{self._namespace}".encode())
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QtCore.QPoint(pixmap.width() // 2, pixmap.height() // 2))
        # Drop on a same-shot primary to promote (dropEvent); drop on a Maya viewport
        # to look through this camera. Viewports aren't Qt drop targets, so we read the
        # viewport under the cursor after the drag rather than trusting its result.
        drag.exec_(_qt.MOVE_ACTION)
        self._controller.look_through_under_cursor(self._namespace)

    # --- drop target (primaries only) ---------------------------------------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        payload = self._payload_for_same_shot(event)
        if payload is None:
            event.ignore()
            return
        self.setStyleSheet(style.CAM_BLOCK_PRIMARY_DROP)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        if self._is_primary:
            self.setStyleSheet(style.CAM_BLOCK_PRIMARY)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        payload = self._payload_for_same_shot(event)
        if payload is None:
            event.ignore()
            return
        _shot_id, namespace = payload
        self.setStyleSheet(style.CAM_BLOCK_PRIMARY)
        event.acceptProposedAction()
        self._controller.promote_to_primary(self._shot_id, namespace)

    def _payload_for_same_shot(
        self, event: QtGui.QDragEnterEvent | QtGui.QDropEvent
    ) -> tuple[str, str] | None:
        """Returns (shot_id, namespace) iff the drag is a valid same-shot promote."""
        if not self._is_primary:
            return None
        mime = event.mimeData()
        if not mime.hasFormat(_MIME_TYPE):
            return None
        raw = bytes(mime.data(_MIME_TYPE)).decode()
        if "|" not in raw:
            return None
        shot_id, namespace = raw.split("|", 1)
        if shot_id != self._shot_id:
            return None
        return shot_id, namespace

    # --- context menu -------------------------------------------------------

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        menu = QMenu(self)
        if not self._is_primary:
            menu.addAction(
                "Promote to primary",
                lambda: self._controller.promote_to_primary(
                    self._shot_id, self._namespace
                ),
            )
        menu.addAction(
            "Rename…",
            lambda: self._controller.rename_camera(self._shot_id, self._namespace),
        )
        menu.addAction(
            "Remove from shot",
            lambda: self._controller.remove_camera(self._shot_id, self._namespace),
        )
        menu.exec_(event.globalPos())


def _frame_label(object_name: str, text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(object_name)
    label.setAttribute(_qt.TRANSPARENT_FOR_MOUSE, True)
    return label
