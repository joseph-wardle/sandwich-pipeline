from __future__ import annotations

import logging
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
from Qt import QtCore
from Qt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pipe.core.ui import DialogButtons
from pipe.core.util.paths import get_production_path

from .anim_index import AnimStream, PublishedAnim, index_key, read_anim_index
from .namespaces import confirm_any_publishable, namespace_of, unpublishable_reason

if TYPE_CHECKING:
    from pipe.core.struct.timeline import Timeline

log = logging.getLogger(__name__)

_DIM = "#8a8a8a"
_ATTENTION = "#e5b340"
_BLOCKED = "#e08282"
_RULE = "rgba(255, 255, 255, 0.13)"

_DIM_STYLE = f"color: {_DIM};"

_ROW_FRAME = "rigRow"

# Only the opening size — the scroll area copes with whatever the fonts and the
# shot's rig count really come to.
_WIDTH = 560
_CHROME_HEIGHT = 180
_ROW_HEIGHT = 32
_MIN_LIST_HEIGHT = 96
_MAX_LIST_HEIGHT = 320


class RigState(Enum):
    PUBLISHED = "published"
    NEVER_PUBLISHED = "never_published"
    RANGE_CHANGED = "range_changed"
    UNPUBLISHABLE = "unpublishable"

    @property
    def included(self) -> bool:
        """Whether this rig starts out marked for publishing."""
        return self is not RigState.UNPUBLISHABLE

    @property
    def locked(self) -> bool:
        """Whether the artist may change their mind about it."""
        return self in (RigState.RANGE_CHANGED, RigState.UNPUBLISHABLE)


_STATUS_COLOR = {
    RigState.PUBLISHED: _DIM,
    RigState.NEVER_PUBLISHED: _DIM,
    RigState.RANGE_CHANGED: _ATTENTION,
    RigState.UNPUBLISHABLE: _BLOCKED,
}

# Main needs no note: it is what Publish means, and a line under every stream
# turns a warning into wallpaper.
_SPLINE_NOTE = "Smooth your animation first! publishing does not smooth it."

_MERGE_BLURB = (
    "Checked rigs are republished. Unchecked rigs keep the animation they already have."
)


@attrs.define(frozen=True)
class RigRow:
    cache_set: str
    label: str
    state: RigState
    status: str
    detail: str
    published: PublishedAnim | None


@attrs.define(frozen=True)
class PublishSelection:
    """What the artist chose in the publish dialog."""

    stream: AnimStream
    sets_to_export: tuple[str, ...] = attrs.field(validator=attrs.validators.min_len(1))
    anims_to_keep: tuple[PublishedAnim, ...]


def select_rigs_to_publish(
    parent: QWidget | None,
    cache_sets: list[str],
    shot_code: str,
    publish_dir: Path,
    timeline: Timeline,
) -> PublishSelection | None:
    """Ask which rigs to publish. Opens a dialog; None means do not publish."""
    if not confirm_any_publishable(parent, cache_sets):
        return None

    dialog = _RigSelectDialog(parent, cache_sets, shot_code, publish_dir, timeline)
    if not dialog.exec_():
        return None
    return dialog.selection()


class _RigSelectDialog(QDialog, DialogButtons):
    """One row per rig, checked to publish it, unchecked to keep what it has."""

    def __init__(
        self,
        parent: QWidget | None,
        cache_sets: list[str],
        shot_code: str,
        publish_dir: Path,
        timeline: Timeline,
    ) -> None:
        super().__init__(parent)
        self._cache_sets = cache_sets
        self._publish_dir = publish_dir
        self._timeline = timeline
        self._rows: list[tuple[RigRow, QCheckBox]] = []

        self._init_buttons(True, "Publish", "Cancel")
        self._publish = self.buttons.button(QDialogButtonBox.Ok)
        self.setWindowTitle(f"Publish Animation — {shot_code}")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.StyledPanel)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {_RULE}; border-radius: 3px; }}"
        )

        self._summary = QLabel()
        self._destination = QLabel()
        self._destination.setStyleSheet(_DIM_STYLE)

        footer = QHBoxLayout()
        footer.addWidget(self._summary)
        footer.addStretch()
        footer.addWidget(self._destination)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addLayout(self._build_header(shot_code))
        layout.addLayout(self._build_stream_picker())
        layout.addWidget(self._scroll)
        layout.addLayout(footer)
        layout.addWidget(self.buttons)

        self._reload()
        self.resize(_WIDTH, _CHROME_HEIGHT + self._list_height())
        # Otherwise the stream radio holds focus, where an arrow key silently
        # republishes against the other stream.
        self._publish.setFocus()

    def _build_header(self, shot_code: str) -> QVBoxLayout:
        title = QLabel("Publish Animation")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        shot = QLabel(shot_code)
        shot.setStyleSheet(_DIM_STYLE)

        line = QHBoxLayout()
        line.addWidget(title)
        line.addStretch()
        line.addWidget(shot)

        blurb = QLabel(_MERGE_BLURB)
        blurb.setStyleSheet(_DIM_STYLE)
        blurb.setWordWrap(True)

        header = QVBoxLayout()
        header.setSpacing(2)
        header.addLayout(line)
        header.addWidget(blurb)
        return header

    def _build_stream_picker(self) -> QVBoxLayout:
        self._main = QRadioButton("Main")
        self._spline = QRadioButton("Spline")
        # Never sticky. A remembered Spline is how stepped animation reaches the
        # sim stream without anyone noticing.
        self._main.setChecked(True)
        self._spline.toggled.connect(self._reload)

        line = QHBoxLayout()
        line.addWidget(QLabel("Publish to:"))
        line.addWidget(self._main)
        line.addWidget(self._spline)
        line.addStretch()

        self._stream_note = QLabel()
        self._stream_note.setStyleSheet(_DIM_STYLE)
        # Reserved rather than shown and hidden, so switching stream does not
        # shunt the rig list up and down under the artist's cursor.
        self._stream_note.setFixedHeight(
            self._stream_note.fontMetrics().lineSpacing() + 2
        )

        picker = QVBoxLayout()
        picker.setSpacing(2)
        picker.addLayout(line)
        picker.addWidget(self._stream_note)
        return picker

    def selection(self) -> PublishSelection:
        export: list[str] = []
        keep: list[PublishedAnim] = []
        for row, box in self._rows:
            if box.isChecked():
                export.append(row.cache_set)
            elif row.published is not None:
                keep.append(row.published)
        return PublishSelection(
            stream=self._stream,
            sets_to_export=tuple(export),
            anims_to_keep=tuple(keep),
        )

    @property
    def _stream(self) -> AnimStream:
        return AnimStream.SPLINE if self._spline.isChecked() else AnimStream.MAIN

    @property
    def _publish_path(self) -> Path:
        return self._publish_dir / self._stream.publish_filename

    def _reload(self) -> None:
        """Re-read the chosen stream's publish and rebuild every row from it."""
        self._stream_note.setText(
            _SPLINE_NOTE if self._stream is AnimStream.SPLINE else ""
        )
        self._destination.setText(_shorten(self._publish_path))
        self._destination.setToolTip(str(self._publish_path))
        # Setting the widget deletes the old rows, so a stream switch starts from
        # this stream's defaults rather than the boxes ticked against the other.
        self._scroll.setWidget(self._build_rows())
        self._update_ready()

    def _build_rows(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._rows = []

        rows = survey_rigs(self._cache_sets, self._publish_path, self._timeline)
        for index, row in enumerate(rows):
            frame, box = self._build_row(row, last=index == len(rows) - 1)
            layout.addWidget(frame)
            self._rows.append((row, box))

        layout.addStretch()
        return container

    def _build_row(self, row: RigRow, last: bool) -> tuple[QFrame, QCheckBox]:
        color = _STATUS_COLOR[row.state]

        box = QCheckBox(row.label)
        box.setChecked(row.state.included)
        box.setEnabled(not row.state.locked)
        box.toggled.connect(self._update_ready)

        status = QLabel(row.status)
        status.setStyleSheet(f"color: {color};")

        line = QHBoxLayout()
        line.addWidget(box)
        line.addStretch()
        line.addWidget(status)

        frame = QFrame()
        frame.setObjectName(_ROW_FRAME)
        # The status says what is happening to the rig; hovering anywhere on the
        # row says why. On screen it would be a paragraph per rig nobody reads.
        frame.setToolTip(row.detail)
        if not last:
            frame.setStyleSheet(
                f"QFrame#{_ROW_FRAME} {{ border-bottom: 1px solid {_RULE}; }}"
            )

        column = QVBoxLayout(frame)
        column.setContentsMargins(10, 6, 10, 6)
        column.addLayout(line)
        return frame, box

    def _list_height(self) -> int:
        listed = _ROW_HEIGHT * len(self._rows)
        return min(max(listed, _MIN_LIST_HEIGHT), _MAX_LIST_HEIGHT)

    def _update_ready(self) -> None:
        publishing = sum(1 for _, box in self._rows if box.isChecked())
        keeping = sum(
            1 for row, box in self._rows if not box.isChecked() and row.published
        )
        self._publish.setEnabled(bool(publishing))

        # Qt delivers no tooltip to a disabled widget, so the footer is where a
        # greyed-out Publish gets to say why.
        if not publishing:
            self._summary.setText("Check a rig to publish.")
            self._summary.setStyleSheet(f"color: {_BLOCKED};")
            return

        kept = f", keeping {keeping}" if keeping else ""
        self._summary.setText(f"Publishing {_rig_count(publishing)}{kept}")
        self._summary.setStyleSheet(_DIM_STYLE)


def survey_rigs(
    cache_sets: list[str], publish_path: Path, timeline: Timeline
) -> list[RigRow]:
    """One row per rig in the scene, in scene order."""
    published = read_anim_index(publish_path)
    return [_row_for(cache_set, published, timeline) for cache_set in cache_sets]


def _row_for(
    cache_set: str, published: dict[str, PublishedAnim], timeline: Timeline
) -> RigRow:
    namespace = namespace_of(cache_set)
    label = namespace or cache_set

    reason = unpublishable_reason(cache_set)
    if reason is not None:
        return RigRow(
            cache_set=cache_set,
            label=label,
            state=RigState.UNPUBLISHABLE,
            status=f"can't publish — {reason.summary}",
            detail=reason.detail,
            published=None,
        )

    entry = published.get(index_key(namespace))
    if entry is None:
        state = RigState.NEVER_PUBLISHED
        status = "never published"
        detail = (
            "This rig has no animation in the shot yet. Uncheck it to leave it "
            "out of the publish, which is what a rig referenced only for "
            "reference wants."
        )
    elif not entry.covers(timeline):
        state = RigState.RANGE_CHANGED
        status = "frame range changed — republishing"
        detail = (
            f"Published over frames {_range_text(entry)}, but the shot now runs "
            f"{timeline.preroll}–{timeline.end}. Republished so every rig in the "
            "shot stays on one timeline."
        )
    else:
        state = RigState.PUBLISHED
        status = f"published {_age_text(entry.modified)}"
        detail = (
            f"Already published over frames {_range_text(entry)}. Leave it "
            "unchecked to keep that animation exactly as it is."
        )

    return RigRow(
        cache_set=cache_set,
        label=label,
        state=state,
        status=status,
        detail=detail,
        published=entry,
    )


def _rig_count(count: int) -> str:
    return f"{count} rig" if count == 1 else f"{count} rigs"


def _shorten(publish_path: Path) -> str:
    """The publish path from the production root down, for a footer that has to fit."""
    try:
        return str(publish_path.relative_to(get_production_path()))
    except ValueError:
        return str(publish_path)


def _range_text(entry: PublishedAnim) -> str:
    if entry.frames is None:
        return "an unrecorded range"
    return "{}–{}".format(*entry.frames)


def _age_text(modified: float) -> str:
    seconds = time.time() - modified
    if seconds < 90:
        return "just now"
    for limit, per_unit, unit in (
        (3600, 60, "minute"),
        (86400, 3600, "hour"),
        (86400 * 30, 86400, "day"),
    ):
        if seconds < limit:
            count = int(seconds // per_unit)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return time.strftime("on %b %-d", time.localtime(modified))
