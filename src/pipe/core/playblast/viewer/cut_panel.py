"""The batch as one deliverable, under the per-clip Confirm panel."""

from __future__ import annotations

from Qt.QtCore import Qt, QThreadPool
from Qt.QtWidgets import (
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.clip import PreviewClip
from pipe.core.playblast.viewer import style
from pipe.core.playblast.viewer.confirm_panel import ConfirmPanel, PanelStatus
from pipe.core.playblast.viewer.playlists import ReviewPlaylists
from pipe.core.ui import FAIL_STYLE

_STATUS_SUFFIX = {
    PanelStatus.CONFIRMED: "  ✓",
    PanelStatus.FAILED: "  ✗",
    PanelStatus.RUNNING: "  …",
}


class CutSection(QWidget):
    """A second Confirm panel whose clip is every clip in the window, joined."""

    _panel: ConfirmPanel | None
    _header: QToolButton | None
    _header_text: str

    def __init__(
        self,
        clip: PreviewClip | None,
        pool: QThreadPool,
        playlists: ReviewPlaylists,
        *,
        clip_count: int,
        unavailable: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._panel = None
        self._header = None
        self._header_text = ""

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(style.PAD_S)

        if clip is None:
            self.setVisible(bool(unavailable))
            column.addWidget(_unavailable_label(unavailable))
            return

        self._header_text = _summary(clip, clip_count)
        self._header = QToolButton()
        self._header.setText(self._header_text)
        self._header.setCheckable(True)
        self._header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.RightArrow)
        self._header.setToolTip(
            "Deliver every clip in this window as one movie, joined in cut order."
        )
        self._header.toggled.connect(self._on_toggled)
        column.addWidget(self._header)

        self._panel = ConfirmPanel(clip, pool, playlists)
        self._panel.state_changed.connect(self._refresh_header)
        self._panel.hide()
        column.addWidget(self._panel)

    def panels(self) -> list[ConfirmPanel]:
        """The cut's Confirm panel, for the window's own bookkeeping; empty when
        there is no cut to deliver."""
        return [] if self._panel is None else [self._panel]

    def _on_toggled(self, expanded: bool) -> None:
        if self._header is None or self._panel is None:
            return
        self._header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._panel.setVisible(expanded)

    def _refresh_header(self) -> None:
        if self._header is None or self._panel is None:
            return
        suffix = _STATUS_SUFFIX.get(self._panel.status, "")
        self._header.setText(f"{self._header_text}{suffix}")


def _unavailable_label(reason: str) -> QLabel:
    label = QLabel(reason)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet(FAIL_STYLE)
    return label


def _summary(clip: PreviewClip, clip_count: int) -> str:
    frames = clip.frame_end - clip.frame_start + 1
    seconds = frames / max(1, clip.fps)
    plural = "" if clip_count == 1 else "s"
    return (
        f"{clip.label}  ·  {clip_count} shot{plural}  ·  {frames}f"
        f"  ·  {int(seconds // 60)}:{int(seconds % 60):02d}"
    )


__all__ = ["CutSection"]
