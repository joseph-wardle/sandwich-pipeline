from __future__ import annotations

import logging
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
from pxr import Sdf
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

from .namespaces import confirm_any_publishable, namespace_of, unpublishable_reason
from .prim_paths import ANIM_CLASS_PATH, RIG_SCOPE_PATH

if TYPE_CHECKING:
    from pipe.core.struct.timeline import Timeline

log = logging.getLogger(__name__)

_DIM = "color: #8a8a8a;"


class AnimStream(Enum):
    """Which of the two parallel anim publishes a run writes."""

    MAIN = "main"
    SPLINE = "spline"

    @property
    def publish_filename(self) -> str:
        return f"{self.value}.usd"


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
        return self is not RigState.PUBLISHED


@attrs.define(frozen=True)
class PublishedAnim:
    """One rig's entry in a shot's existing anim publish."""

    namespace: str
    anim_layer: Path
    # Publishes made before the index gained its rig scope hold animation but
    # record no rig asset.
    rig_asset: Path | None
    frames: tuple[int, int] | None
    modified: float

    def covers(self, timeline: Timeline) -> bool:
        return self.frames == (timeline.preroll, timeline.end)


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
    sets_to_export: list[str]
    namespaces_to_keep: list[str]


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
        self.resize(520, 400)

        self._main = QRadioButton("Main")
        self._spline = QRadioButton("Spline — smoothed copy for sims")
        # Never sticky. A remembered Spline is how stepped animation reaches the
        # sim stream without anyone noticing.
        self._main.setChecked(True)
        self._spline.toggled.connect(self._reload)

        streams = QHBoxLayout()
        streams.addWidget(QLabel("Publish to:"))
        streams.addWidget(self._main)
        streams.addWidget(self._spline)
        streams.addStretch()

        self._precondition = QLabel(
            "Publishes your scene as it is now. Smooth your animation before "
            "publishing."
        )
        self._precondition.setStyleSheet(_DIM)
        self._precondition.setWordWrap(True)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)

        self._footer = QLabel()
        self._footer.setStyleSheet(_DIM)

        layout = QVBoxLayout(self)
        layout.addLayout(streams)
        layout.addWidget(self._precondition)
        layout.addWidget(self._scroll)
        layout.addWidget(self._footer)
        layout.addWidget(self.buttons)

        self._reload()
        # Otherwise the stream radio holds focus, where an arrow key silently
        # republishes against the other stream.
        self._publish.setFocus()

    def selection(self) -> PublishSelection:
        export: list[str] = []
        keep: list[str] = []
        for row, box in self._rows:
            if box.isChecked():
                export.append(row.cache_set)
            elif row.published is not None:
                keep.append(row.published.namespace)
        return PublishSelection(
            stream=self._stream, sets_to_export=export, namespaces_to_keep=keep
        )

    @property
    def _stream(self) -> AnimStream:
        return AnimStream.SPLINE if self._spline.isChecked() else AnimStream.MAIN

    @property
    def _publish_path(self) -> Path:
        return self._publish_dir / self._stream.publish_filename

    def _reload(self) -> None:
        """Re-read the chosen stream's publish and rebuild every row from it."""
        self._precondition.setVisible(self._stream is AnimStream.SPLINE)
        self._footer.setToolTip(str(self._publish_path))
        # Setting the widget deletes the old rows, so a stream switch starts from
        # this stream's defaults rather than the boxes ticked against the other.
        self._scroll.setWidget(self._build_rows())
        self._update_ready()

    def _build_rows(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        self._rows = []

        for row in survey_rigs(self._cache_sets, self._publish_path, self._timeline):
            box = QCheckBox(row.label)
            box.setChecked(row.state.included)
            box.setEnabled(not row.state.locked)
            box.setToolTip(row.detail)
            box.toggled.connect(self._update_ready)

            status = QLabel(row.status)
            status.setStyleSheet(_DIM)
            status.setToolTip(row.detail)

            line = QHBoxLayout()
            line.addWidget(box)
            line.addStretch()
            line.addWidget(status)
            layout.addLayout(line)

            self._rows.append((row, box))

        layout.addStretch()
        return container

    def _update_ready(self) -> None:
        ready = any(box.isChecked() for _, box in self._rows)
        self._publish.setEnabled(ready)
        # Qt delivers no tooltip to a disabled widget, so the footer is where a
        # greyed-out Publish gets to say why.
        self._footer.setText(
            _shorten(self._publish_path) if ready else "Check a rig to publish."
        )


def survey_rigs(
    cache_sets: list[str], publish_path: Path, timeline: Timeline
) -> list[RigRow]:
    """One row per rig in the scene, in scene order."""
    published = read_published_anim(publish_path)
    return [_row_for(cache_set, published, timeline) for cache_set in cache_sets]


def read_published_anim(publish_path: Path) -> dict[str, PublishedAnim]:
    """What the shot's current anim publish holds, keyed by rig namespace."""
    if not publish_path.is_file():
        return {}

    layer = _open_layer(publish_path)
    if layer is None:
        return {}

    anim_scope = layer.GetPrimAtPath(ANIM_CLASS_PATH)
    if anim_scope is None:
        log.warning(
            "'%s' has no %s — treating it as empty", publish_path, ANIM_CLASS_PATH
        )
        return {}

    folder = publish_path.parent
    entries: dict[str, PublishedAnim] = {}
    for spec in anim_scope.nameChildren:
        anim_layer = _referenced_asset(spec, folder)
        if anim_layer is None:
            log.warning(
                "'%s' indexes rig '%s' without referencing any animation; ignoring it",
                publish_path,
                spec.name,
            )
            continue
        try:
            modified = anim_layer.stat().st_mtime
        except OSError:
            # The shot has already lost this rig's animation, so republishing is
            # the fix. Dropping it here is what makes the row say so.
            log.warning(
                "'%s' indexes rig '%s' as '%s', which is not on disk; ignoring it",
                publish_path,
                spec.name,
                anim_layer,
            )
            continue
        entries[spec.name] = PublishedAnim(
            namespace=spec.name,
            anim_layer=anim_layer,
            rig_asset=_referenced_asset(
                layer.GetPrimAtPath(RIG_SCOPE_PATH.AppendChild(spec.name)), folder
            ),
            frames=_published_frames(anim_layer),
            modified=modified,
        )
    return entries


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
            status=reason.summary,
            detail=(
                f"{reason.detail}, so its animation cannot be exported. "
                "Reference the rig directly into the shot to publish it."
            ),
            published=None,
        )

    entry = published.get(namespace)
    if entry is None:
        state = RigState.NEVER_PUBLISHED
        status = "never published"
        detail = "This rig has no animation in the shot yet, so it is always included."
    elif not entry.covers(timeline):
        state = RigState.RANGE_CHANGED
        status = "frame range changed"
        detail = (
            f"Published over frames {_range_text(entry)}, but this shot now runs "
            f"{timeline.preroll}–{timeline.end}. It is always included so the "
            "shot's rigs stay on one timeline."
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


def _open_layer(path: Path, *, metadata_only: bool = False) -> Sdf.Layer | None:
    """The layer as it is on disk."""
    try:
        layer = Sdf.Layer.OpenAsAnonymous(str(path), metadataOnly=metadata_only)
    except Exception:
        log.warning("Could not open '%s'", path, exc_info=True)
        return None
    return layer or None


def _referenced_asset(spec: Sdf.PrimSpec | None, folder: Path) -> Path | None:
    """The first asset a prim references, resolved against the publish's folder."""
    if spec is None:
        return None
    for reference in spec.referenceList.GetAddedOrExplicitItems():
        if reference.assetPath:
            return folder / reference.assetPath
    return None


def _published_frames(anim_layer: Path) -> tuple[int, int] | None:
    """The frame range `UsdUtils.StitchClips` stamped into a stitched layer."""
    layer = _open_layer(anim_layer, metadata_only=True)
    if layer is None or not (layer.HasStartTimeCode() and layer.HasEndTimeCode()):
        return None
    return int(layer.startTimeCode), int(layer.endTimeCode)


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
