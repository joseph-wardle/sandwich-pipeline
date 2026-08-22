"""The timeline view: primary takes drawn where their material lives in scene time."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import maya.cmds as mc
from Qt import QtGui
from Qt.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import _qt, active, cameras, packing, style
from .cam_block import BLOCK_HEIGHT, CamBlock
from .playhead import Playhead
from .ruler import RULER_HEIGHT, Ruler
from .state import PrevisShot, PrevisState

if TYPE_CHECKING:
    from .panel import PrevisPanel

_BLOCK_MARGIN_Y = 4


# ----- geometry (pure) ------------------------------------------------------


def source_span(shots: Sequence[PrevisShot]) -> tuple[int, int]:
    """First and last scene frame any shot's material occupies."""
    return min(s.source_in for s in shots), max(s.source_out for s in shots)


def frame_to_x(frame: int, first_frame: int, px_per_frame: int) -> int:
    return (frame - first_frame) * px_per_frame


def track_height(row_height: int) -> int:
    """Vertical pitch between tracks."""
    return max(row_height, BLOCK_HEIGHT + 2 * _BLOCK_MARGIN_Y)


def block_span(
    source_in: int, duration: int, *, first_frame: int, px_per_frame: int
) -> tuple[int, int]:
    """`(x, width)` for a source range. The sole frame-to-pixel authority on this axis."""
    return frame_to_x(source_in, first_frame, px_per_frame), duration * px_per_frame


def block_rect(
    shot: PrevisShot,
    track: int,
    *,
    first_frame: int,
    px_per_frame: int,
    row_height: int,
) -> tuple[int, int, int, int]:
    """`(x, y, w, h)` for one shot's block, on the track packing gave it."""
    pitch = track_height(row_height)
    x, width = block_span(
        shot.source_in,
        shot.primary_duration,
        first_frame=first_frame,
        px_per_frame=px_per_frame,
    )
    return (
        x,
        RULER_HEIGHT + track * pitch + _BLOCK_MARGIN_Y,
        width,
        pitch - 2 * _BLOCK_MARGIN_Y,
    )


def content_size(
    span: tuple[int, int], tracks: Sequence[int], *, px_per_frame: int, row_height: int
) -> tuple[int, int]:
    first_frame, last_frame = span
    width = (last_frame - first_frame + 1) * px_per_frame
    height = RULER_HEIGHT + (max(tracks) + 1) * track_height(row_height)
    return width, height


class TimelineView(QWidget):
    def __init__(
        self,
        controller: PrevisPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setStyleSheet(f"background: {style.PANEL_BG};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(_qt.SCROLL_AS_NEEDED)
        self._scroll.setVerticalScrollBarPolicy(_qt.SCROLL_AS_NEEDED)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {style.PANEL_BG}; border: 0; }}"
        )
        outer.addWidget(self._scroll)

        # A sibling of the scroll area rather than a positioned child of it:
        # with nothing to scroll there is no content to hang a geometry off.
        self._empty = QLabel("No shots yet.  Click  + shot  to create one.", self)
        self._empty.setAlignment(_qt.ALIGN_CENTER)
        self._empty.setStyleSheet(
            f"color: {style.PANEL_TEXT_DIM}; padding: 40px; font-size: 12px;"
        )
        self._empty.hide()
        outer.addWidget(self._empty)

        # No layout: blocks sit at arbitrary frames, so each one is positioned
        # outright rather than fitted into cells.
        self._inner = QWidget()
        self._inner.setStyleSheet(f"background: {style.PANEL_BG};")
        self._scroll.setWidget(self._inner)

        self._playhead = Playhead(self._inner)

        self._blocks_by_shot: dict[str, CamBlock] = {}
        self._span = (0, 0)
        # Held because the widget's own height lags a rebuild by an event loop
        # turn, and the playhead has to span the tracks drawn *now*.
        self._content_height = 0
        self._row_height = style.ROW_HEIGHT_DEFAULT
        self._px_per_frame = style.PX_PER_FRAME_DEFAULT
        self._last_state: PrevisState | None = None

        self.setToolTip(
            "Drag a block to move its source in · drag its left edge to trim the head\n"
            "Ctrl+Wheel: zoom vertically · Shift+Wheel: zoom horizontally"
        )

    def set_state(self, state: PrevisState) -> None:
        self._last_state = state
        self._clear()
        self._blocks_by_shot = {}
        self._empty.setVisible(not state.shots)
        self._scroll.setVisible(bool(state.shots))
        if not state.shots:
            self._playhead.hide()
            return

        self._span = source_span(state.shots)
        tracks = packing.assign_tracks(state.shots)
        width, self._content_height = content_size(
            self._span,
            tracks,
            px_per_frame=self._px_per_frame,
            row_height=self._row_height,
        )
        # The scroll area sizes its widget from this, which is what makes the
        # content scroll instead of being squeezed into the viewport.
        self._inner.setMinimumSize(width, self._content_height)

        self._add_ruler(width)
        for index, (shot, track) in enumerate(zip(state.shots, tracks)):
            self._add_shot(shot, track, cut_index=index)
        self.apply_selection(active.selected_shot_id())
        self.sync_playhead()

    def apply_selection(self, shot_id: str | None) -> None:
        for candidate_id, block in self._blocks_by_shot.items():
            block.set_selected(candidate_id == shot_id)

    def sync_playhead(self) -> None:
        """Stand the playhead on the current scene frame."""
        if self._last_state is None or not self._last_state.shots:
            self._playhead.hide()
            return
        first_frame, last_frame = self._span
        frame = int(mc.currentTime(query=True))
        if not first_frame <= frame <= last_frame:
            self._playhead.hide()
            return
        self._playhead.move_to(
            frame_to_x(frame, first_frame, self._px_per_frame),
            max(self._content_height, self._inner.height()),
        )

    def preview_resize(self, shot_id: str, namespace: str, new_length: int) -> None:
        """Live block width during a right-edge drag; the left edge is pinned."""
        shot = self._shot(shot_id)
        if shot is None or shot.primary != namespace:
            return
        self._preview_block(shot_id, source_in=shot.source_in, length=new_length)

    def preview_span(
        self, shot_id: str, *, start_delta: int, length_delta: int
    ) -> None:
        """Live block geometry for a proposed source range: body drag or head trim."""
        shot = self._shot(shot_id)
        if shot is not None:
            self._preview_block(
                shot_id,
                source_in=shot.source_in + start_delta,
                length=shot.primary_duration + length_delta,
            )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_playhead()  # keep the line spanning the (re)sized track stack

    # ----- private ---------------------------------------------------------

    def _shot(self, shot_id: str) -> PrevisShot | None:
        return None if self._last_state is None else self._last_state.find_shot(shot_id)

    def _preview_block(self, shot_id: str, *, source_in: int, length: int) -> None:
        """Redraw one block from in-flight numbers, without touching state."""
        block = self._blocks_by_shot.get(shot_id)
        if block is None:
            return
        geometry = block.geometry()
        x, width = block_span(
            source_in,
            length,
            first_frame=self._span[0],
            px_per_frame=self._px_per_frame,
        )
        block.setGeometry(x, geometry.y(), width, geometry.height())

    def _add_ruler(self, width: int) -> None:
        ruler = Ruler(self._inner, on_scrub=self._controller.scrub_to_frame)
        ruler.set_range(*self._span)
        # Sized to the content, not stretched to fit, so its own width/range
        # mapping reduces to the same px-per-frame the blocks are placed with.
        ruler.setGeometry(0, 0, width, RULER_HEIGHT)
        ruler.show()

    def _add_shot(self, shot: PrevisShot, track: int, *, cut_index: int) -> None:
        take = shot.primary_take
        badge = f"#{cut_index + 1}"
        widget: QWidget
        if take is None:
            widget = self._placeholder(shot, badge)
        else:
            block = CamBlock(
                namespace=take.namespace,
                is_primary=True,
                length_frames=take.duration,
                # Scene time, unlike the cut view: here the block's own position
                # already says where in the scene its material starts.
                start_frame=shot.source_in,
                shot_id=shot.id,
                controller=self._controller,
                height=self._block_height(),
                px_per_frame=self._px_per_frame,
                missing=not cameras.is_live(take.namespace),
                # Geometry here says nothing about edit order, so the block has
                # to carry it.
                badge=badge,
                # Unlocks body-drag and the head handle: on this axis both edit
                # a number the block is already showing.
                source_axis=True,
                parent=self._inner,
            )
            self._blocks_by_shot[shot.id] = block
            widget = block

        widget.setGeometry(
            *block_rect(
                shot,
                track,
                first_frame=self._span[0],
                px_per_frame=self._px_per_frame,
                row_height=self._row_height,
            )
        )
        widget.show()

    def _placeholder(self, shot: PrevisShot, badge: str) -> QLabel:
        """Stand-in for a shot whose last take was removed."""
        label = QLabel(f"{badge}  {shot.code or 'no code'}  ·  no camera", self._inner)
        label.setAlignment(_qt.ALIGN_CENTER)
        label.setStyleSheet(style.EMPTY_SHOT_BLOCK)
        label.setToolTip(
            f"cut {badge}  ·  {shot.code or 'no code'}\n"
            f"{shot.source_in} → {shot.source_out}  ({shot.primary_duration}f)\n"
            f"no camera — add a take from the cut view"
        )
        return label

    def _block_height(self) -> int:
        return track_height(self._row_height) - 2 * _BLOCK_MARGIN_Y

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        # A zoom rebuilds every block, so mid-drag it would delete the widget that
        # still owes us a release. Asked of Qt, so the guard cannot get stuck.
        if QApplication.mouseButtons():
            event.accept()
            return
        mods = event.modifiers()
        ctrl = bool(mods & _qt.CONTROL)
        shift = bool(mods & _qt.SHIFT)
        if not (ctrl or shift):
            super().wheelEvent(event)
            return
        self._row_height, self._px_per_frame = style.zoom_step(
            self._row_height,
            self._px_per_frame,
            vertical=ctrl,
            up=event.angleDelta().y() > 0,
        )
        if self._last_state is not None:
            self.set_state(self._last_state)
        event.accept()

    def _clear(self) -> None:
        """Drop every positioned child; the playhead is kept so a rebuild never
        disturbs it."""
        for child in list(self._inner.children()):
            if isinstance(child, QWidget) and child is not self._playhead:
                child.setParent(None)
                child.deleteLater()
