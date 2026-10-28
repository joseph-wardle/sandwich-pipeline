"""The column beside the video: each clip's Confirm panel, the cut below them."""

from __future__ import annotations

from Qt.QtCore import QThreadPool, Signal
from Qt.QtWidgets import QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer import style
from pipe.core.playblast.viewer.confirm_panel import ConfirmPanel, PanelStatus
from pipe.core.playblast.viewer.cut_panel import CutSection
from pipe.core.playblast.viewer.playlists import ReviewPlaylists

WIDTH = 300

# Clips that still have something to deliver.
UNCONFIRMED = (PanelStatus.PENDING, PanelStatus.FAILED)


class ConfirmColumn(QWidget):
    """One Confirm panel per clip, in the window's clip order."""

    state_changed = Signal()
    # The index of a clip whose every checked destination landed.
    delivered = Signal(int)

    panels: list[ConfirmPanel]
    _pool: QThreadPool
    _playlists: ReviewPlaylists
    _stack: QStackedWidget
    _cut_section: CutSection

    def __init__(
        self,
        clips: list[PreviewClip],
        *,
        cut: PreviewClip | None,
        cut_unavailable: str,
    ) -> None:
        super().__init__()
        # Confirm jobs run here, one at a time — parallel deliveries would
        # race on encode CPU and previs take numbering. The window owns the
        # pool (not the DCC-global one) so viewer work never mixes with the
        # host application's.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._playlists = ReviewPlaylists(self)

        self.panels = []
        self._stack = QStackedWidget()
        for clip in clips:
            panel = ConfirmPanel(clip, self._pool, self._playlists)
            panel.state_changed.connect(self.state_changed)
            # Looked up on delivery, since an action can remove an earlier clip.
            panel.delivered.connect(
                lambda p=panel: self.delivered.emit(self.panels.index(p))
            )
            self.panels.append(panel)
            self._stack.addWidget(panel)
        # A batch with nothing to confirm (unmigrated DCC flows) keeps the
        # plain view-only window.
        self._stack.setVisible(self._has_confirmables())

        self._cut_section = CutSection(
            cut,
            self._pool,
            self._playlists,
            clip_count=len(clips),
            unavailable=cut_unavailable,
        )
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(style.PAD_M)
        column.addWidget(self._stack, stretch=1)
        column.addWidget(self._cut_section)
        self.setFixedWidth(WIDTH)
        self.setVisible(self._has_confirmables() or self._cut_section.isVisible())

    def _has_confirmables(self) -> bool:
        return any(panel.is_confirmable for panel in self.panels)

    def ask_to_close(self) -> bool:
        """Whether the window may close, asking first if a clip would be
        left undelivered."""
        # A running delivery is already writing files and pipeline records;
        # closing mid-flight would abandon it half-done, so it can't be
        # discarded — only waited out.
        panels = [*self.panels, *self._cut_section.panels()]
        if any(panel.status is PanelStatus.RUNNING for panel in panels):
            QMessageBox.information(
                self,
                "Playblast Viewer",
                "A delivery is still running. Wait for it to finish, then close.",
            )
            return False
        count = sum(panel.status in UNCONFIRMED for panel in self.panels)
        if not count:
            return True
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
        return reply == QMessageBox.StandardButton.Discard

    def show_clip(self, index: int) -> None:
        self._stack.setCurrentIndex(index)

    def remove(self, index: int) -> None:
        panel = self.panels.pop(index)
        self._stack.removeWidget(panel)
        panel.deleteLater()


__all__ = ["UNCONFIRMED", "WIDTH", "ConfirmColumn"]
