"""What to render, and how good it should look: the previs playblast settings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from Qt.QtCore import Qt
from Qt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.ui import DialogButtons
from pipe.dcc.maya.playblast.viewport import ViewportQuality, query_viewport_quality

# Label, `ViewportQuality` field, and what the artist gets for ticking it. The
# order is the order they appear in, left to right.
_QUALITY_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("Anti-aliasing", "anti_alias", "Smooth edges instead of stair-stepping them."),
    ("Lighting", "lighting", "Light the shot with the scene's lights."),
    ("Shadows", "shadows", "Cast viewport shadows from those lights."),
    (
        "Ambient Occlusion",
        "ssao",
        "Shade contact areas with screen-space ambient occlusion.",
    ),
    ("Hardware Fog", "hardware_fog", "Include the viewport's hardware fog."),
    ("Depth of Field", "dof", "Defocus by the camera's depth of field."),
)
_QUALITY_COLUMNS = 3


@dataclass(frozen=True)
class PlayblastRow:
    """One line of the playblast checklist."""

    shot_id: str
    label: str
    detail: str
    blocker: str | None


@dataclass(frozen=True)
class PlayblastRequest:
    """The artist's answer: which shots to render, and how to render them."""

    shot_ids: list[str]
    quality: ViewportQuality


class _PlayblastDialog(QDialog, DialogButtons):
    """Pick which shots to render and what the capture should include."""

    _list: QListWidget
    _quality: dict[str, QCheckBox]

    def __init__(self, parent: QWidget | None, rows: Sequence[PlayblastRow]) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Playblast", "Cancel")
        self.setWindowTitle("Playblast Shots")
        self.setMinimumWidth(460)

        self._list = QListWidget()
        for row in rows:
            self._list.addItem(_playblast_item(row))
        self._list.itemChanged.connect(self._refresh_ok)

        all_button = QPushButton("All")
        all_button.clicked.connect(lambda: self._set_all(True))
        none_button = QPushButton("None")
        none_button.clicked.connect(lambda: self._set_all(False))
        select = QHBoxLayout()
        select.addWidget(all_button)
        select.addWidget(none_button)
        select.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Shots to render, in cut order:"))
        layout.addWidget(self._list)
        layout.addLayout(select)
        layout.addWidget(self._build_quality_group())
        layout.addWidget(self.buttons)
        self._refresh_ok()

    def request(self) -> PlayblastRequest:
        return PlayblastRequest(shot_ids=self._chosen_ids(), quality=self._quality_of())

    def _build_quality_group(self) -> QGroupBox:
        group = QGroupBox("Quality")
        group.setToolTip(
            "Starts from your current viewport. What you tick here is what the "
            "capture renders."
        )
        grid = QGridLayout(group)
        current = asdict(query_viewport_quality())
        self._quality = {}
        for index, (label, field, tooltip) in enumerate(_QUALITY_OPTIONS):
            box = QCheckBox(label)
            box.setChecked(bool(current[field]))
            box.setToolTip(tooltip)
            grid.addWidget(box, index // _QUALITY_COLUMNS, index % _QUALITY_COLUMNS)
            self._quality[field] = box
        return group

    def _quality_of(self) -> ViewportQuality:
        # Spelled out rather than splatted: a renamed field should break here,
        # where the table is, not silently at runtime.
        checked = {field: box.isChecked() for field, box in self._quality.items()}
        return ViewportQuality(
            anti_alias=checked["anti_alias"],
            dof=checked["dof"],
            hardware_fog=checked["hardware_fog"],
            lighting=checked["lighting"],
            shadows=checked["shadows"],
            ssao=checked["ssao"],
        )

    def _chosen_ids(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._items()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def _items(self) -> list[QListWidgetItem]:
        return [self._list.item(row) for row in range(self._list.count())]

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._items():
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(state)

    def _refresh_ok(self) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self._chosen_ids()))


def _playblast_item(row: PlayblastRow) -> QListWidgetItem:
    text = f"{row.label}  ·  {row.detail}"
    item = QListWidgetItem(text if row.blocker is None else f"{text}  —  {row.blocker}")
    item.setData(Qt.ItemDataRole.UserRole, row.shot_id)
    if row.blocker is None:
        item.setCheckState(Qt.CheckState.Checked)
        return item
    item.setCheckState(Qt.CheckState.Unchecked)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
    item.setToolTip(row.blocker)
    return item


def ask_playblast(
    parent: QWidget | None, rows: Sequence[PlayblastRow]
) -> PlayblastRequest | None:
    """The shots to render and the quality to render them at, or None on cancel."""
    dialog = _PlayblastDialog(parent, rows)
    if not dialog.exec_():
        return None
    return dialog.request()


__all__ = [
    "PlayblastRequest",
    "PlayblastRow",
    "ask_playblast",
]
