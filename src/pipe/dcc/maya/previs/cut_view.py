"""The cut view: shots as columns in edit order, takes stacked vertically."""

from __future__ import annotations

from typing import TYPE_CHECKING

import maya.cmds as mc
from Qt import QtGui
from Qt.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import _qt, active, cameras, dialogs, style
from .add_alt_button import AddAltButton
from .cam_block import BLOCK_HEIGHT, CamBlock
from .playhead import Playhead
from .ruler import RULER_HEIGHT, Ruler
from .shot_header import HEADER_HEIGHT, ShotHeader
from .state import FRAME_START, PrevisShot, PrevisState, ShotTake

if TYPE_CHECKING:
    from .panel import PrevisPanel

TRACK_LABEL_WIDTH = 76

_ROW_RULER = 0
_ROW_HEADERS = 1
_ROW_PRIMARY = 2  # alt rows follow: _ROW_PRIMARY + 1 + alt_index


class CutView(QWidget):
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

        self._inner = QWidget()
        self._inner.setStyleSheet(f"background: {style.PANEL_BG};")
        self._scroll.setWidget(self._inner)

        # Free child (not in the grid) so a rebuild never disturbs it.
        self._playhead = Playhead(self._inner)

        self._grid = QGridLayout(self._inner)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(0)
        self._grid.setColumnMinimumWidth(0, TRACK_LABEL_WIDTH)

        # Remember last build's extent so the next rebuild can clear its
        # min-widths and trailing stretch on shrinks.
        self._prior_shot_count = 0
        self._prior_deepest_row = _ROW_PRIMARY
        self._ruler: Ruler | None = None  # held so resize previews can reflow ticks
        # Headers carry the selection highlight; cut starts map a shot to its
        # place on the cut axis. Both are rebuilt with the grid.
        self._headers_by_shot: dict[str, ShotHeader] = {}
        self._cut_starts: dict[str, int] = {}
        # Alt blocks per shot, kept so a primary resize-drag can re-pin each
        # alt's width and toggle truncated styling live without rebuilding.
        self._alt_blocks_by_shot: dict[str, list[CamBlock]] = {}

        # Zoom state (Ctrl+wheel = vertical, Shift+wheel = horizontal).
        self._row_height = style.ROW_HEIGHT_DEFAULT
        self._px_per_frame = style.PX_PER_FRAME_DEFAULT
        self._last_state: PrevisState | None = None

        self.setToolTip("Ctrl+Wheel: zoom vertically · Shift+Wheel: zoom horizontally")

    # ----- public ----------------------------------------------------------

    def set_state(self, state: PrevisState) -> None:
        self._last_state = state
        self._clear()
        self._reset_layout_overrides()
        self._alt_blocks_by_shot = {}
        self._headers_by_shot = {}
        self._cut_starts = state.cut_starts()
        if not state.shots:
            empty = QLabel("No shots yet.  Click  + shot  to create one.", self._inner)
            empty.setStyleSheet(
                f"color: {style.PANEL_TEXT_DIM}; padding: 40px; font-size: 12px;"
            )
            empty.setAlignment(_qt.ALIGN_CENTER)
            self._grid.addWidget(empty, 0, 0, 1, 2)
            self._playhead.hide()
            return

        shots = state.shots
        column_lengths = [s.primary_duration for s in shots]
        first_frame = FRAME_START
        last_frame = first_frame + sum(column_lengths) - 1

        self._apply_column_widths(column_lengths)
        self._add_ruler_row(first_frame, last_frame, num_shots=len(shots))
        self._add_header_row(shots)
        deepest_row = self._add_track_rows(shots)
        self._apply_trailing_stretches(len(shots), deepest_row)
        self.apply_selection(active.selected_shot_id())
        self.sync_playhead()

    def apply_selection(self, shot_id: str | None) -> None:
        for candidate_id, header in self._headers_by_shot.items():
            header.set_selected(candidate_id == shot_id)

    def sync_playhead(self) -> None:
        """Draw the playhead inside the active shot's column; hide it in a gap."""
        state = self._last_state
        if state is None or not state.shots:
            self._playhead.hide()
            return
        frame = int(mc.currentTime(query=True))
        shot = active.active_shot(state, frame)
        cut_frame = None if shot is None else state.cut_frame(shot, frame)
        if cut_frame is None:
            self._playhead.hide()
            return
        x = TRACK_LABEL_WIDTH + self._cut_width(cut_frame - FRAME_START)
        self._playhead.move_to(x, self._inner.height())

    def _scrub_from_cut(self, cut_frame: int) -> None:
        """Ruler click: find the shot at that cut frame, then scrub to its scene frame."""
        state = self._last_state
        found = None if state is None else state.shot_at_cut(cut_frame)
        if found is None:
            return
        shot, source_frame = found
        # Select first: the shot the artist just clicked is the one that should
        # own the frame, even where several shots overlap it.
        self._controller.select_shot(shot.id)
        self._controller.scrub_to_frame(source_frame)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_playhead()  # keep the line spanning the (re)sized track stack

    def _reset_layout_overrides(self) -> None:
        """Undo min-widths and trailing stretches from the previous build."""
        for col in range(1, self._prior_shot_count + 2):
            self._grid.setColumnMinimumWidth(col, 0)
            self._grid.setColumnStretch(col, 0)
        self._grid.setRowStretch(self._prior_deepest_row + 1, 0)

    def _cut_width(self, frames: int) -> int:
        """The cut axis' only frame-to-pixel map."""
        return frames * self._px_per_frame

    def _apply_column_widths(self, shot_lengths: list[int]) -> None:
        for index, length in enumerate(shot_lengths):
            self._grid.setColumnMinimumWidth(index + 1, self._cut_width(length))

    def _apply_trailing_stretches(self, num_shots: int, deepest_row: int) -> None:
        """Trailing col + row absorb slack; otherwise QGridLayout stretches content."""
        self._grid.setColumnStretch(num_shots + 1, 1)
        self._grid.setRowStretch(deepest_row + 1, 1)
        self._prior_shot_count = num_shots
        self._prior_deepest_row = deepest_row

    def _add_ruler_row(
        self, first_frame: int, last_frame: int, *, num_shots: int
    ) -> None:
        spacer = QFrame(self._inner)
        spacer.setFixedSize(TRACK_LABEL_WIDTH, RULER_HEIGHT)
        spacer.setStyleSheet(
            f"background: {style.PANEL_BG_DEEP}; "
            f"border-right: 1px solid {style.PANEL_BORDER};"
        )
        self._grid.addWidget(spacer, _ROW_RULER, 0)

        ruler = Ruler(self._inner, on_scrub=self._scrub_from_cut)
        ruler.set_range(first_frame, last_frame)
        self._grid.addWidget(ruler, _ROW_RULER, 1, 1, num_shots)
        self._ruler = ruler

    def _add_header_row(self, shots: list[PrevisShot]) -> None:
        spacer = QFrame(self._inner)
        spacer.setFixedSize(TRACK_LABEL_WIDTH, HEADER_HEIGHT)
        spacer.setStyleSheet(
            f"background: {style.PANEL_BG_DEEP}; "
            f"border-right: 1px solid {style.PANEL_BORDER};"
        )
        self._grid.addWidget(spacer, _ROW_HEADERS, 0)
        for index, shot in enumerate(shots):
            header = ShotHeader(
                shot=shot,
                controller=self._controller,
                parent=self._inner,
            )
            self._grid.addWidget(header, _ROW_HEADERS, index + 1)
            self._headers_by_shot[shot.id] = header

    def _add_track_rows(self, shots: list[PrevisShot]) -> int:
        """Build the primary row, alt rows, and per-shot add-alt cell. Returns the deepest row index used."""
        max_alts = max((len(s.other_takes) for s in shots), default=0)

        # Primary row
        self._grid.addWidget(_track_label("Primary", is_primary=True), _ROW_PRIMARY, 0)
        self._grid.setRowMinimumHeight(_ROW_PRIMARY, self._row_height)
        for index, shot in enumerate(shots):
            self._grid.addLayout(
                self._cell_for_camera(shot, shot.primary_take, is_primary=True),
                _ROW_PRIMARY,
                index + 1,
            )

        # Alt rows
        for alt_index in range(max_alts):
            grid_row = _ROW_PRIMARY + 1 + alt_index
            self._grid.addWidget(_track_label("", is_primary=False), grid_row, 0)
            self._grid.setRowMinimumHeight(grid_row, self._row_height)
            for index, shot in enumerate(shots):
                others = shot.other_takes
                if alt_index < len(others):
                    self._grid.addLayout(
                        self._cell_for_camera(
                            shot, others[alt_index], is_primary=False
                        ),
                        grid_row,
                        index + 1,
                    )

        # Add-alt row: one cell per shot, positioned right under that shot's last alt.
        for index, shot in enumerate(shots):
            grid_row = _ROW_PRIMARY + 1 + len(shot.other_takes)
            self._grid.setRowMinimumHeight(grid_row, self._row_height)
            if not self._grid.itemAtPosition(grid_row, 0):
                self._grid.addWidget(_track_label("", is_primary=False), grid_row, 0)
            self._grid.addLayout(
                self._add_alt_cell(shot.id, enabled=bool(shot.primary)),
                grid_row,
                index + 1,
            )

        return max(_ROW_PRIMARY + 1 + len(s.other_takes) for s in shots)

    def _cell_for_camera(
        self,
        shot: PrevisShot,
        take: ShotTake | None,
        *,
        is_primary: bool,
    ) -> QHBoxLayout:
        cell = QHBoxLayout()
        cell.setContentsMargins(1, 4, 1, 4)
        cell.setSpacing(0)
        if take is None:
            cell.addStretch(1)
            return cell

        cam_length = take.duration
        primary_length = shot.primary_duration
        block = CamBlock(
            namespace=take.namespace,
            is_primary=is_primary,
            length_frames=cam_length,
            # Every label this block prints counts up from here, and it is
            # measured against a cut ruler — so a scene frame here would lie.
            start_frame=self._cut_starts.get(shot.id, FRAME_START),
            shot_id=shot.id,
            controller=self._controller,
            height=self._block_height(),
            px_per_frame=self._px_per_frame,
            truncated=(not is_primary) and cam_length > primary_length,
            missing=not cameras.is_live(take.namespace),
            parent=self._inner,
        )

        if is_primary:
            # Primary grows with its column (stretch=1, no width pin).
            cell.addWidget(block, 1)
            return cell

        # Alts always pin a fixed width + trailing stretch — uniform structure
        # so `preview_resize` can re-pin live during a primary drag.
        block.setFixedWidth(self._alt_visual_width(primary_length, cam_length))
        cell.addWidget(block, 0)
        cell.addStretch(1)
        self._alt_blocks_by_shot.setdefault(shot.id, []).append(block)
        return cell

    def _alt_visual_width(self, primary_length: int, cam_length: int) -> int:
        """Pixel width for an alt block sharing a column sized to `primary_length`.

        Shorter alts show their real length; exact and truncated alts fill the
        column edge-to-edge and say the rest with a chevron.
        """
        return self._cut_width(min(cam_length, primary_length))

    def _add_alt_cell(self, shot_id: str, *, enabled: bool) -> QHBoxLayout:
        cell = QHBoxLayout()
        cell.setContentsMargins(1, 4, 1, 4)
        cell.setSpacing(0)
        button = AddAltButton(self._inner)
        button.setEnabled(enabled)
        button.setStyleSheet(style.ADD_ALT_CELL)
        button.setFixedHeight(self._block_height())
        button.setCursor(_qt.POINTING_HAND)
        button.clicked.connect(lambda: self._open_add_alt(shot_id))
        cell.addWidget(button)
        return cell

    def _block_height(self) -> int:
        """Track-block height tracks row height with a small breathing margin."""
        return max(BLOCK_HEIGHT, self._row_height - 8)

    def _open_add_alt(self, shot_id: str) -> None:
        dialogs.show_add_alternate_menu(
            self,
            on_new_rig=lambda: self._controller.add_take_new_rig(shot_id),
            on_duplicate=lambda: self._controller.add_take_duplicate_primary(shot_id),
            on_existing=lambda: self._controller.add_take_existing_camera(shot_id),
        )

    # ----- live preview (driven by CamBlock during drag) -------------------

    def preview_resize(self, shot_id: str, namespace: str, new_length: int) -> None:
        """Reflow column min-width, ruler ticks, and alt-block sizing during a primary drag.

        Alt-block resizes don't drive column width (column is primary-keyed), so
        we only reflow when the resized camera is its shot's primary.
        """
        if self._last_state is None:
            return
        index = next(
            (i for i, s in enumerate(self._last_state.shots) if s.id == shot_id), -1
        )
        if index < 0:
            return
        shot = self._last_state.shots[index]
        if shot.primary != namespace:
            return  # alt resize — column unchanged
        self._grid.setColumnMinimumWidth(index + 1, self._cut_width(new_length))
        for alt_block in self._alt_blocks_by_shot.get(shot_id, []):
            alt_length = alt_block.length_frames
            alt_block.setFixedWidth(self._alt_visual_width(new_length, alt_length))
            alt_block.set_truncated(alt_length > new_length)
        if self._ruler is not None:
            total_frames = sum(
                new_length if i == index else s.primary_duration
                for i, s in enumerate(self._last_state.shots)
            )
            self._ruler.set_range(FRAME_START, FRAME_START + total_frames - 1)
            self._ruler.update()

    # ----- wheel zoom ------------------------------------------------------

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

    # ----- teardown --------------------------------------------------------

    def _clear(self) -> None:
        _drain(self._grid)


def _drain(layout: QLayout) -> None:
    """Recursively delete every widget under `layout`; nested QLayouts are GC'd."""
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            _drain(sub)


def _track_label(text: str, *, is_primary: bool) -> QLabel:
    label = QLabel(text)
    label.setFixedWidth(TRACK_LABEL_WIDTH)
    label.setStyleSheet(
        style.TRACK_LABEL_PRIMARY if is_primary else style.TRACK_LABEL_ALT
    )
    return label
