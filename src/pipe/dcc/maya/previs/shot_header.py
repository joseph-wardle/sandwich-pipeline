"""Shot header strip: sticky-code label + break-out dot / cam pip + per-shot menu."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pipe.core.util.paths import get_production_path
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

from . import _qt, status, style
from .state import PrevisShot

if TYPE_CHECKING:
    from .panel import PrevisPanel

HEADER_HEIGHT = 32

# Human-readable state labels for the tooltip.
_RLO_LABEL: dict[str, str] = {
    status.RLO_NO_CODE: "no code",
    status.RLO_READY: "ready",
    status.RLO_DRIFTED: "drifted",
    status.RLO_IN_SYNC: "in sync",
}
_CAM_LABEL: dict[str, str] = {
    status.CAM_ABSENT_STALE: "absent / stale",
    status.CAM_IN_SYNC: "in sync",
}

# Dot/pip fill colors. `no_code` is absent — it renders as a dashed outline (see
# `_build_dot`), so it has no fill.
_RLO_COLOR: dict[str, str] = {
    status.RLO_READY: style.RLO_READY,
    status.RLO_DRIFTED: style.RLO_DRIFTED,
    status.RLO_IN_SYNC: style.RLO_IN_SYNC,
}
_CAM_COLOR: dict[str, str] = {
    status.CAM_ABSENT_STALE: style.CAM_ABSENT_STALE,
    status.CAM_IN_SYNC: style.CAM_IN_SYNC,
}


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

        prod_root = get_production_path()
        self._rlo = status.rlo_state(shot, prod_root)
        self._cam = status.cam_state(shot, prod_root)

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
        self._pip = self._build_pip()
        layout.addWidget(self._pip)
        self._shotgrid_label = self._build_shotgrid_label()
        layout.addWidget(self._shotgrid_label)
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
        self._shotgrid_label.setVisible(full)
        self._pip.setVisible(compact)
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
        if self._rlo == status.RLO_NO_CODE:
            dot.setStyleSheet(
                f"QFrame {{ background: transparent; "
                f"border: 1px dashed {style.RLO_NO_CODE}; border-radius: 4px; }}"
            )
        else:
            color = _RLO_COLOR[self._rlo]
            dot.setStyleSheet(f"QFrame {{ background: {color}; border-radius: 4px; }}")
        return dot

    def _build_pip(self) -> QFrame:
        pip = QFrame(self)
        pip.setFixedSize(6, 6)
        pip.setAttribute(_qt.TRANSPARENT_FOR_MOUSE)
        color = _CAM_COLOR[self._cam]
        pip.setStyleSheet(f"QFrame {{ background: {color}; border-radius: 3px; }}")
        return pip

    def _build_shotgrid_label(self) -> QLabel:
        if self._rlo == status.RLO_NO_CODE:
            label = QLabel("unlinked", self)
            label.setStyleSheet(
                f"color: {style.RLO_NO_CODE}; font-size: 11px; font-style: italic;"
            )
        else:
            label = QLabel(self._shot.shotgrid_code or "", self)
            label.setStyleSheet(f"color: {style.PANEL_TEXT_DIM}; font-size: 11px;")
        label.setAttribute(_qt.TRANSPARENT_FOR_MOUSE)
        return label

    def _tooltip_text(self) -> str:
        shotgrid = self._shot.shotgrid_code or "unlinked"
        rlo_label = _RLO_LABEL[self._rlo]
        cam_label = _CAM_LABEL[self._cam]
        return (
            f"code: {self._shot.code or '—'}\n"
            f"shotgrid: {shotgrid}\n"
            f"break-out: {rlo_label}\n"
            f"cam: {cam_label}"
        )

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
            "Break out to RLO", lambda: self._controller.break_out_shot(self._shot.id)
        )
        menu.addAction(
            "Publish shot camera",
            lambda: self._controller.publish_shot_camera(self._shot.id),
        )
        menu.addAction(
            "Export take", lambda: self._controller.export_take(self._shot.id)
        )
        menu.addSeparator()
        menu.addAction(
            "Delete shot", lambda: self._controller.remove_shot(self._shot.id)
        )
        menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))
