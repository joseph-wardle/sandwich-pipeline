from __future__ import annotations

import logging
import time
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
from Qt import QtCore
from Qt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pipe.core.publish import Target
from pipe.core.shot import shot_target
from pipe.core.ui import DialogButtons, PublishChoice, PublishRows

from .anim_index import (
    DEPARTMENT,
    PublishedAnim,
    index_key,
    published_frames,
    read_anim_index,
)
from .namespaces import UnpublishableReason, namespace_of, unpublishable_reason

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot
    from pipe.core.struct.timeline import Timeline

log = logging.getLogger(__name__)

_DIM = "#8a8a8a"
_ATTENTION = "#e5b340"
_BLOCKED = "#e08282"

_DIM_STYLE = f"color: {_DIM};"

_WIDTH = 560
_GROUP_GAP = 6


class RigState(Enum):
    PUBLISHED = "published"
    NEVER_PUBLISHED = "never_published"
    RANGE_CHANGED = "range_changed"
    UNPUBLISHABLE = "unpublishable"
    ABSENT = "absent"

    @property
    def included(self) -> bool:
        """Whether this rig starts out marked for publishing."""
        return self not in (RigState.UNPUBLISHABLE, RigState.ABSENT)

    @property
    def locked(self) -> bool:
        """Whether the artist may change their mind about it."""
        return self in (
            RigState.RANGE_CHANGED,
            RigState.UNPUBLISHABLE,
            RigState.ABSENT,
        )


_STATUS_COLOR = {
    RigState.PUBLISHED: _DIM,
    RigState.NEVER_PUBLISHED: _DIM,
    RigState.RANGE_CHANGED: _ATTENTION,
    RigState.UNPUBLISHABLE: _BLOCKED,
    RigState.ABSENT: _DIM,
}

_MERGE_BLURB = (
    "Checked rigs are republished. Unchecked rigs keep the animation they already have."
)

_SHARED_NAME = UnpublishableReason(
    "shares a name with another rig",
    "Another rig in this scene has the same name apart from capitals, and a rig "
    "is identified by its name. Rename one of the references so each rig has "
    "its own.",
)


@attrs.define(frozen=True)
class RigRow:
    label: str
    state: RigState
    status: str
    detail: str
    # None when the shot's publish holds this rig but the scene does not, which
    # is the one row the artist cannot republish.
    cache_set: str | None
    published: PublishedAnim | None


@attrs.define(frozen=True)
class PublishSelection:
    """What the artist chose in the publish dialog."""

    sets_to_export: tuple[str, ...] = attrs.field(validator=attrs.validators.min_len(1))
    anims_to_keep: tuple[PublishedAnim, ...]
    # The version the dialog said this publish becomes.
    target: Target
    choice: PublishChoice


def select_rigs_to_publish(
    parent: QWidget | None,
    cache_sets: list[str],
    shot: Shot,
    timeline: Timeline,
) -> PublishSelection | None:
    """Ask which rigs to publish. Opens a dialog; None means do not publish."""
    dialog = _RigSelectDialog(parent, cache_sets, shot, timeline)
    if not dialog.exec_():
        return None
    return dialog.selection()


class _RigSelectDialog(QDialog, DialogButtons):
    """One row per rig, checked to publish it, unchecked to keep what it has."""

    def __init__(
        self,
        parent: QWidget | None,
        cache_sets: list[str],
        shot: Shot,
        timeline: Timeline,
    ) -> None:
        super().__init__(parent)
        self._target = shot_target(shot, DEPARTMENT)
        self._rows: list[tuple[RigRow, QCheckBox]] = []

        self._init_buttons(True, "Publish", "Cancel")
        self._publish = self.buttons.button(QDialogButtonBox.Ok)
        self.setWindowTitle("Publish Animation")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        blurb = QLabel(_MERGE_BLURB)
        blurb.setStyleSheet(_DIM_STYLE)
        blurb.setWordWrap(True)

        rigs = self._build_rows(survey_rigs(cache_sets, self._target.current, timeline))

        self._summary = QLabel()
        self._publish_rows = PublishRows(self._target.label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(blurb)
        layout.addWidget(rigs)
        layout.addWidget(self._summary)
        layout.addSpacing(_GROUP_GAP)
        layout.addWidget(self._publish_rows)
        layout.addStretch()
        layout.addWidget(self.buttons)

        self._update_ready()
        # As tall as the layout asks at this width, which the blurb wraps to.
        self.resize(_WIDTH, self.heightForWidth(_WIDTH))
        self._publish_rows.setFocus()

    def selection(self) -> PublishSelection:
        export: list[str] = []
        keep: list[PublishedAnim] = []
        for row, box in self._rows:
            if row.cache_set is not None and box.isChecked():
                export.append(row.cache_set)
            elif row.published is not None:
                keep.append(row.published)
        return PublishSelection(
            sets_to_export=tuple(export),
            anims_to_keep=tuple(keep),
            target=self._target,
            choice=self._publish_rows.choice(),
        )

    def _build_rows(self, rows: list[RigRow]) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        for row in rows:
            line, box = self._build_row(row)
            layout.addWidget(line)
            self._rows.append((row, box))
        return container

    def _build_row(self, row: RigRow) -> tuple[QWidget, QCheckBox]:
        box = QCheckBox(row.label)
        box.setChecked(row.state.included)
        box.setEnabled(not row.state.locked)
        box.toggled.connect(self._update_ready)

        status = QLabel(row.status)
        status.setStyleSheet(f"color: {_STATUS_COLOR[row.state]};")

        line = QWidget()
        # The status says what is happening to the rig; hovering anywhere on the
        # row says why. On screen it would be a paragraph per rig nobody reads.
        line.setToolTip(row.detail)
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        layout.addStretch()
        layout.addWidget(status)
        return line, box

    def _update_ready(self) -> None:
        publishing = sum(1 for _, box in self._rows if box.isChecked())
        keeping = sum(
            1 for row, box in self._rows if not box.isChecked() and row.published
        )
        self._publish.setEnabled(bool(publishing))

        # Qt delivers no tooltip to a disabled widget, so the footer is where a
        # greyed-out Publish gets to say why.
        if not publishing:
            anything_publishable = any(
                row.state is not RigState.UNPUBLISHABLE and row.cache_set is not None
                for row, _ in self._rows
            )
            self._summary.setText(
                "Check a rig to publish."
                if anything_publishable
                else "No rig in this scene can be published — hover a row to see why."
            )
            self._summary.setStyleSheet(f"color: {_BLOCKED};")
            return

        kept = f", keeping {keeping}" if keeping else ""
        self._summary.setText(f"Publishing {_rig_count(publishing)}{kept}")
        self._summary.setStyleSheet(_DIM_STYLE)


def survey_rigs(
    cache_sets: list[str], current: Path, timeline: Timeline
) -> list[RigRow]:
    """One row per rig: the scene's rigs in scene order, then any the shot has
    published that the scene no longer holds."""
    published = read_anim_index(current)
    keys = {cache_set: index_key(namespace_of(cache_set)) for cache_set in cache_sets}
    shared = {key for key, count in Counter(keys.values()).items() if count > 1}

    rows = [
        _row_for(
            cache_set,
            unpublishable_reason(cache_set)
            or (_SHARED_NAME if keys[cache_set] in shared else None),
            published.get(keys[cache_set]),
            timeline,
        )
        for cache_set in cache_sets
    ]
    indexed = set(keys.values())
    rows += [
        _absent_row(entry) for key, entry in published.items() if key not in indexed
    ]
    return rows


def _row_for(
    cache_set: str,
    reason: UnpublishableReason | None,
    entry: PublishedAnim | None,
    timeline: Timeline,
) -> RigRow:
    label = namespace_of(cache_set) or cache_set

    if reason is not None:
        kept = " Its published animation is kept as it is." if entry else ""
        return RigRow(
            label=label,
            state=RigState.UNPUBLISHABLE,
            status=f"can't publish — {reason.summary}",
            detail=reason.detail + kept,
            cache_set=cache_set,
            published=entry,
        )

    if entry is None:
        state = RigState.NEVER_PUBLISHED
        status = "never published"
        detail = (
            "This rig has no animation in the shot yet. Uncheck it to leave it "
            "out of the publish, which is what a rig referenced only for "
            "reference wants."
        )
    elif (frames := published_frames(entry.anim_layer)) != (
        timeline.preroll,
        timeline.end,
    ):
        state = RigState.RANGE_CHANGED
        status = "frame range changed — republishing"
        detail = (
            f"Published over frames {_range_text(frames)}, but the shot now runs "
            f"{timeline.preroll}–{timeline.end}. Republished so every rig in the "
            "shot stays on one timeline."
        )
    else:
        state = RigState.PUBLISHED
        status = f"published {_age_text(entry.anim_layer)}"
        detail = (
            f"Already published over frames {_range_text(frames)}. Leave it "
            "unchecked to keep that animation exactly as it is."
        )

    return RigRow(
        label=label,
        state=state,
        status=status,
        detail=detail,
        cache_set=cache_set,
        published=entry,
    )


def _absent_row(entry: PublishedAnim) -> RigRow:
    return RigRow(
        label=entry.name,
        state=RigState.ABSENT,
        status="not in this scene — keeping",
        detail=(
            "The shot has published animation for this rig, but it is not in "
            "the scene — unloaded, or removed since it was published. The "
            "publish carries its animation forward untouched. Load the "
            "reference back in if you meant to replace it."
        ),
        cache_set=None,
        published=entry,
    )


def _rig_count(count: int) -> str:
    return f"{count} rig" if count == 1 else f"{count} rigs"


def _range_text(frames: tuple[int, int] | None) -> str:
    return "an unrecorded range" if frames is None else "{}–{}".format(*frames)


def _age_text(anim_layer: Path) -> str:
    try:
        modified = anim_layer.stat().st_mtime
    except OSError:
        return "at an unknown time"
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
