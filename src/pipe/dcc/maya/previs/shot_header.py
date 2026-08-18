"""Shot header strip: sticky-code label + break-out dot + per-shot menu."""

from __future__ import annotations

from typing import TYPE_CHECKING

from Qt import QtCore, QtGui
from Qt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from . import _qt, rlo, style
from .state import PrevisShot

if TYPE_CHECKING:
    from .panel import PrevisPanel

HEADER_HEIGHT = 32


class ShotHeader(QFrame):
    def __init__(
        self,
        *,
        shot: PrevisShot,
        controller: PrevisPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shot = shot
        self._controller = controller

        self._broken_out = rlo.is_broken_out(shot)

        self.setFixedHeight(HEADER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAutoFillBackground(True)
        self.setCursor(_qt.POINTING_HAND)  # clicking the header jumps the playhead here
        self.setStyleSheet(
            f"ShotHeader {{ background: {style.PANEL_BG_HEADER}; "
            f"border-right: 1px solid {style.PANEL_BORDER_SOFT}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(6)

        self._code_label = self._build_code_label()
        layout.addWidget(self._code_label, 1)
        self._dot = self._build_dot()
        layout.addWidget(self._dot)
        self._menu_btn = self._build_menu_button()
        layout.addWidget(self._menu_btn)

        self.setToolTip(self._tooltip_text())

    # Both hints return width=1 so the column shrinks to its setColumnMinimumWidth.
    # See CamBlock.minimumSizeHint for the QGridLayout gotcha this avoids.
    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, HEADER_HEIGHT)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, HEADER_HEIGHT)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        # Clicking anywhere on the strip jumps the playhead to this shot; the menu
        # button eats its own clicks. Code editing lives in the hamburger menu, not here.
        if event.button() == _qt.LEFT_BUTTON:
            self._controller.jump_to_shot(self._shot.id)
        super().mousePressEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        w = self.width()
        full = w >= style.TIER_COMPACT
        compact = w >= style.TIER_NARROW
        self._code_label.setVisible(full)
        self._menu_btn.setVisible(compact)
        # Dot is the load-bearing state signal — always show it.
        self._dot.setVisible(True)

    def _build_code_label(self) -> QLabel:
        label = QLabel(self._shot.code or "—", self)
        label.setStyleSheet(
            f"color: {style.PANEL_TEXT}; font-size: 12px; "
            f"font-weight: 500; letter-spacing: 1px;"
        )
        return label

    def _build_dot(self) -> QFrame:
        dot = QFrame(self)
        dot.setFixedSize(8, 8)
        dot.setAttribute(_qt.TRANSPARENT_FOR_MOUSE)  # clicks fall through to the strip
        fill = (
            f"background: {style.RLO_BROKEN_OUT}"
            if self._broken_out
            else f"background: transparent; border: 1px dashed {style.RLO_PENDING}"
        )
        dot.setStyleSheet(f"QFrame {{ {fill}; border-radius: 4px; }}")
        return dot

    def _tooltip_text(self) -> str:
        broken_out = "yes" if self._broken_out else "not yet"
        return f"code: {self._shot.code or '—'}\nbroken out to RLO: {broken_out}"

    def _build_menu_button(self) -> QPushButton:
        btn = QPushButton("⋮", self)
        btn.setFixedWidth(18)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {style.PANEL_TEXT_DIM}; "
            f"border: 0; font-size: 14px; }} "
            f"QPushButton:hover {{ color: {style.PANEL_TEXT}; }}"
        )
        btn.clicked.connect(self._open_menu)
        return btn

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(
            "Set code…", lambda: self._controller.declare_code(self._shot.id)
        )
        menu.addSeparator()
        menu.addAction(
            "Move left", lambda: self._controller.move_shot(self._shot.id, -1)
        )
        menu.addAction(
            "Move right", lambda: self._controller.move_shot(self._shot.id, 1)
        )
        menu.addSeparator()
        menu.addAction(
            "Playblast shot", lambda: self._controller.playblast_shot(self._shot.id)
        )
        menu.addSeparator()
        menu.addAction(
            "Delete shot", lambda: self._controller.remove_shot(self._shot.id)
        )
        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))
