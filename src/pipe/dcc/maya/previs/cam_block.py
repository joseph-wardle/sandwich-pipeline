"""Single camera block.

Double-click a primary to look through it or an alt to promote it,
drag an alt onto its column's primary to promote, right-click for a menu, drag
the right edge to set the shot's length.
"""

from __future__ import annotations

from collections.abc import Callable
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

# Content the block must keep beyond its handles, below which there is nothing
# left to grab from and the handles' stripes crowd out the colored sliver.
_HANDLE_MIN_CONTENT = 18


class _EdgeHandle(QFrame):
    """Edge grabber for primary blocks, reporting its drag in pixels."""

    def __init__(
        self,
        block: CamBlock,
        *,
        preview: Callable[[int], None],
        commit: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._block = block
        self._preview = preview
        self._commit = commit
        self.setObjectName("resizeHandle")
        self.setFixedWidth(_HANDLE_WIDTH)
        self.setCursor(QCursor(_qt.SIZE_HOR))
        self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        self._drag_active = False
        self._drag_start_global_x = 0

    @property
    def is_dragging(self) -> bool:
        return self._drag_active

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
        self._block.select()
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag_active:
            super().mouseMoveEvent(event)
            return
        self._preview(event.globalPos().x() - self._drag_start_global_x)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag_active:
            super().mouseReleaseEvent(event)
            return
        self._drag_active = False
        self.setStyleSheet(style.RESIZE_HANDLE_IDLE)
        delta_px = event.globalPos().x() - self._drag_start_global_x
        # Preview first so a drag that rounded back to zero still lands its
        # labels on the real numbers; a commit rebuilds the block anyway.
        self._preview(delta_px)
        self._commit(delta_px)
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
        missing: bool = False,
        badge: str = "",
        source_axis: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("camBlock")
        self._namespace = namespace
        self._is_primary = is_primary
        self._length_frames = length_frames
        self._start_frame = start_frame
        self._truncated = truncated
        self._missing = missing
        self._badge = badge
        self._source_axis = source_axis
        self._selected = False
        self._badge_label: QLabel | None = None
        # Taken verbatim from the timeline so drag math stays stable —
        # deriving px-per-frame from self.width() drifts because the block is
        # being live-resized during the drag.
        self._px_per_frame = max(1, px_per_frame)
        self._shot_id = shot_id
        self._controller = controller
        self._height_hint = height  # used by minimumSizeHint before geometry resolves
        self._handles: list[_EdgeHandle] = []
        self._press_pos: QtCore.QPoint | None = None
        self._press_global_x = 0
        self._body_drag_active = False

        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Without WA_StyledBackground, Qt's native style ignores stylesheet
        # background-color and shows outline-only.
        self.setAttribute(_qt.STYLED_BACKGROUND, True)
        self.setStyleSheet(self._block_style())
        if is_primary:
            # A missing primary still takes drops: promoting a live alt onto it is
            # one of the two ways an artist repairs the shot.
            self.setAcceptDrops(True)

        self._head_handle = is_primary and source_axis
        if source_axis:
            self.setCursor(QCursor(_qt.SIZE_ALL))  # the body is draggable here

        left, right = self._content_margins()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(left, 0, right, 0)
        outer.setSpacing(0)
        if self._head_handle:
            outer.addWidget(
                self._add_handle(self.preview_head_drag, self.commit_head_drag)
            )
        outer.addLayout(self._build_content(), 1)
        if is_primary:
            outer.addWidget(
                self._add_handle(self.preview_tail_drag, self.commit_tail_drag)
            )

        self.setToolTip(self._tooltip_text(self._start_frame, self._length_frames))

    def _add_handle(
        self, preview: Callable[[int], None], commit: Callable[[int], None]
    ) -> _EdgeHandle:
        handle = _EdgeHandle(self, preview=preview, commit=commit, parent=self)
        self._handles.append(handle)
        return handle

    def _content_margins(self) -> tuple[int, int]:
        """Left/right padding; a handle's side gives up its margin to the handle.

        The primary's thick border-left already eats ~2px, so it asks for less.
        """
        return (
            0 if self._head_handle else (8 if self._is_primary else 10),
            0 if self._is_primary else 10,
        )

    def _block_style(self) -> str:
        if self._missing:
            return style.CAM_BLOCK_MISSING
        if self._is_primary:
            return (
                style.CAM_BLOCK_PRIMARY_SELECTED
                if self._selected
                else style.CAM_BLOCK_PRIMARY
            )
        return style.CAM_BLOCK_ALT_TRUNC if self._truncated else style.CAM_BLOCK_ALT

    def set_selected(self, selected: bool) -> None:
        """Mark this block as the shot that wins an overlap."""
        if selected == self._selected:
            return
        self._selected = selected
        self.setStyleSheet(self._block_style())

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

        col.addLayout(self._build_name_row())
        col.addLayout(self._build_frame_row())
        return col

    def _build_name_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        if self._badge:
            self._badge_label = QLabel(self._badge, self)
            self._badge_label.setAttribute(_qt.TRANSPARENT_FOR_MOUSE, True)
            self._badge_label.setStyleSheet(style.CUT_BADGE)
            row.addWidget(self._badge_label)

        # Decorative labels — `WA_TransparentForMouseEvents` lets presses fall
        # through to the QFrame so the block's own mousePressEvent fires.
        self._name_label = QLabel(self._namespace, self)
        self._name_label.setObjectName("name")
        self._name_label.setAttribute(_qt.TRANSPARENT_FOR_MOUSE, True)
        if self._missing:
            font = self._name_label.font()
            font.setStrikeOut(True)
            self._name_label.setFont(font)
        row.addWidget(self._name_label, 1)
        return row

    def _build_frame_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._start_label = _frame_label("startFrame", str(self._start_frame), self)
        self._length_label = _frame_label(
            "lengthBadge", f"{self._length_frames}f", self
        )
        self._end_label = _frame_label(
            "endFrame", self._end_text(self._end_frame()), self
        )

        row.addWidget(self._start_label)
        row.addStretch(1)
        row.addWidget(self._length_label)
        row.addStretch(1)
        row.addWidget(self._end_label)
        return row

    def _end_frame(self) -> int:
        return self._start_frame + max(self._length_frames - 1, 0)

    def _end_text(self, end: int) -> str:
        return f"{end} ›››" if self._truncated else str(end)

    def _tooltip_text(self, start: int, length: int) -> str:
        end = start + max(length - 1, 0)
        suffix = "  (longer than primary)" if self._truncated else ""
        lines = [self._namespace]
        if self._badge:
            lines.append(f"cut {self._badge}")
        lines.append(f"{start} → {end}  ({length}f){suffix}")
        if self._missing:
            lines.append("camera missing from the scene — right-click to re-link")
        return "\n".join(lines)

    @property
    def length_frames(self) -> int:
        return self._length_frames

    def set_truncated(self, truncated: bool) -> None:
        """Swap alt-block stylesheet + end-label chevron when the truncated state flips."""
        if truncated == self._truncated:
            return
        self._truncated = truncated
        self.setStyleSheet(self._block_style())
        self._show_frames(self._start_frame, self._length_frames)

    # --- tier-based progressive disclosure ---------------------------------

    def _apply_tier(self) -> None:
        """Show/hide labels and the edge handles based on current width.

        - ≥ COMPACT: full layout, name elided to fit.
        - NARROW–COMPACT: only the centered duration pill.
        - < NARROW: just a colored sliver; hover tooltip carries the info.
        """
        w = self.width()
        # Tail first, so when only one handle fits it is the one every block has.
        for depth, handle in enumerate(reversed(self._handles), start=1):
            room = _HANDLE_WIDTH * depth + _HANDLE_MIN_CONTENT
            handle.setVisible(handle.is_dragging or w >= room)
        full = w >= style.TIER_COMPACT
        compact = w >= style.TIER_NARROW
        if self._badge_label is not None:
            self._badge_label.setVisible(compact)
        self._name_label.setVisible(full)
        self._start_label.setVisible(full)
        self._end_label.setVisible(full)
        self._length_label.setVisible(compact)
        if full:
            self._elide_name()

    def _elide_name(self) -> None:
        outer_left, outer_right = self._content_margins()
        handle_w = sum(_HANDLE_WIDTH for h in self._handles if h.isVisible())
        badge_w = (
            self._badge_label.sizeHint().width() + 6
            if self._badge_label is not None
            else 0
        )
        available = self.width() - outer_left - outer_right - handle_w - badge_w
        if available <= 0:
            return
        fm = self._name_label.fontMetrics()
        self._name_label.setText(
            fm.elidedText(self._namespace, _qt.ELIDE_RIGHT, available)
        )

    def preview_tail_drag(self, delta_px: int) -> None:
        length = self._new_length(delta_px)
        self._show_frames(self._start_frame, length)
        self._controller.preview_resize_camera(self._shot_id, self._namespace, length)

    def commit_tail_drag(self, delta_px: int) -> None:
        length = self._new_length(delta_px)
        if length != self._length_frames:
            self._controller.resize_camera(self._shot_id, self._namespace, length)

    def preview_head_drag(self, delta_px: int) -> None:
        delta = self._trim_frames(delta_px)
        self._preview_span(start_delta=delta, length_delta=-delta)

    def commit_head_drag(self, delta_px: int) -> None:
        delta = self._trim_frames(delta_px)
        if delta:
            self._controller.trim_head(self._shot_id, delta)

    def preview_body_drag(self, delta_px: int) -> None:
        self._preview_span(start_delta=self._delta_frames(delta_px), length_delta=0)

    def commit_body_drag(self, delta_px: int) -> None:
        delta = self._delta_frames(delta_px)
        if delta:
            self._controller.set_source_in(self._shot_id, self._start_frame + delta)

    def _preview_span(self, *, start_delta: int, length_delta: int) -> None:
        """Both source-axis drags: a proposed source range, with state untouched."""
        self._show_frames(
            self._start_frame + start_delta, self._length_frames + length_delta
        )
        self._controller.preview_span(
            self._shot_id, start_delta=start_delta, length_delta=length_delta
        )

    def _show_frames(self, start: int, length: int) -> None:
        """Point the frame labels at a range, stored or merely proposed."""
        self._start_label.setText(str(start))
        self._length_label.setText(f"{length}f")
        self._end_label.setText(self._end_text(start + max(length - 1, 0)))
        self.setToolTip(self._tooltip_text(start, length))

    def _delta_frames(self, delta_px: int) -> int:
        return int(round(delta_px / self._px_per_frame))

    def _new_length(self, delta_px: int) -> int:
        return max(1, self._length_frames + self._delta_frames(delta_px))

    def _trim_frames(self, delta_px: int) -> int:
        """Head movement in frames, floored so the shot always keeps one frame."""
        return min(self._length_frames - 1, self._delta_frames(delta_px))

    def select(self) -> None:
        """Make this block's shot the one that wins an overlap."""
        self._controller.select_shot(self._shot_id)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == _qt.LEFT_BUTTON:
            self.select()
            self._press_pos = event.pos()
            self._press_global_x = event.globalPos().x()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._body_drag_active:
            self.preview_body_drag(event.globalPos().x() - self._press_global_x)
            event.accept()
            return
        if self._press_pos is not None and event.buttons() & _qt.LEFT_BUTTON:
            travel = (event.pos() - self._press_pos).manhattanLength()
            if travel >= QApplication.startDragDistance():
                if self._source_axis:
                    self._body_drag_active = True
                    self.preview_body_drag(event.globalPos().x() - self._press_global_x)
                else:
                    self._start_drag()
                    self._press_pos = None
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._body_drag_active:
            self._body_drag_active = False
            delta_px = event.globalPos().x() - self._press_global_x
            self.preview_body_drag(delta_px)  # a drag that rounded to zero still lands
            self.commit_body_drag(delta_px)
            self._press_pos = None
            event.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != _qt.LEFT_BUTTON:
            super().mouseDoubleClickEvent(event)
            return
        if self._is_primary:
            self._controller.look_through(self._namespace)
        else:
            self._controller.promote_to_primary(self._shot_id, self._namespace)
        event.accept()

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
            self.setStyleSheet(self._block_style())
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        payload = self._payload_for_same_shot(event)
        if payload is None:
            event.ignore()
            return
        _shot_id, namespace = payload
        self.setStyleSheet(self._block_style())
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
        if self._missing:
            menu.addAction(
                "Re-link…",
                lambda: self._controller.relink_camera(self._shot_id, self._namespace),
            )
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
