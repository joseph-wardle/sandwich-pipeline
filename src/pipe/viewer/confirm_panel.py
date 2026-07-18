"""The Confirm panel: one clip's destination checklist and Confirm button."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path

import attrs
from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast.confirm import (
    SEND_TO_EDIT_DESTINATION_NAME,
    SHOTGRID_DESTINATION_NAME,
    ConfirmChoices,
    ConfirmResult,
    confirm_clip,
)
from pipe.core.playblast.preview_spec import (
    Destination,
    PreviewClip,
    PrevisStamp,
    ShotGridUpload,
)
from pipe.core.playblast.review.playlists import list_recent_review_playlists
from pipe.viewer.settings import (
    load_checked_destinations,
    load_last_custom_folder,
    save_checked_destinations,
    save_last_custom_folder,
)
from pipe.viewer import style

log = logging.getLogger(__name__)


class RowState(Enum):
    IDLE = auto()
    RUNNING = auto()
    DELIVERED = auto()
    FAILED = auto()


class PanelStatus(Enum):
    VIEW_ONLY = auto()
    PENDING = auto()
    RUNNING = auto()
    CONFIRMED = auto()
    FAILED = auto()


class _JobSignals(QObject):
    finished = Signal(object)  # ConfirmResult
    failed = Signal(str)


class _ConfirmJob(QRunnable):
    """Runs one `confirm_clip` call off the GUI thread."""

    signals: _JobSignals
    _work: Callable[[], ConfirmResult]

    def __init__(self, work: Callable[[], ConfirmResult]) -> None:
        super().__init__()
        # The panel holds a reference until the job reports back, so Python
        # keeps ownership of the C++ runnable.
        self.setAutoDelete(False)
        self.signals = _JobSignals()
        self._work = work

    def run(self) -> None:
        try:
            self.signals.finished.emit(self._work())
        except Exception as exc:
            log.exception("Confirm failed before any delivery")
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class _Row(QWidget):
    """One checklist row: checkbox, status mark, inline failure detail."""

    toggled = Signal()

    _state: RowState
    _checkbox: QCheckBox
    _status: QLabel
    _detail: QLabel
    _header: QHBoxLayout
    _column: QVBoxLayout

    def __init__(self, name: str) -> None:
        super().__init__()
        self._state = RowState.IDLE
        self._checkbox = QCheckBox(name)
        self._checkbox.toggled.connect(lambda _checked: self._on_toggled())
        self._status = QLabel("")
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(style.FAIL_STYLE)
        self._detail.hide()

        self._header = QHBoxLayout()
        self._header.addWidget(self._checkbox)
        self._header.addStretch(1)
        self._header.addWidget(self._status)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.addLayout(self._header)
        self._column.addWidget(self._detail)

    def _on_toggled(self) -> None:
        self.toggled.emit()

    @property
    def name(self) -> str:
        return self._checkbox.text()

    @property
    def state(self) -> RowState:
        return self._state

    @property
    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        self._checkbox.setChecked(checked)

    def set_running(self) -> None:
        self._state = RowState.RUNNING
        self._status.setText("…")
        self._status.setStyleSheet("")
        self._detail.hide()

    def set_idle(self) -> None:
        self._state = RowState.IDLE
        self._status.setText("")

    def set_delivered(self, detail: str) -> None:
        self._state = RowState.DELIVERED
        self._status.setText("✓")
        self._status.setStyleSheet(style.OK_STYLE)
        self._status.setToolTip(detail)
        self._checkbox.setEnabled(False)
        self._detail.hide()

    def set_failed(self, detail: str) -> None:
        self._state = RowState.FAILED
        self._status.setText("✗")
        self._status.setStyleSheet(style.FAIL_STYLE)
        self._detail.setText(detail)
        self._detail.show()


class _FolderRow(_Row):
    """A disk-folder destination. Browsable rows let the artist pick the
    directory; the picked folder is remembered globally across tools."""

    _destination: Destination
    _directory: Path
    _path_label: QLabel

    def __init__(self, destination: Destination) -> None:
        super().__init__(destination.name)
        self._destination = destination
        self._directory = destination.directory
        if destination.browsable:
            self._directory = load_last_custom_folder() or destination.directory
            self._path_label = QLabel()
            self._show_directory()
            browse = QPushButton("Browse…")
            browse.clicked.connect(self._on_browse)
            self._header.insertWidget(2, self._path_label)
            self._header.insertWidget(3, browse)
        else:
            self._checkbox.setToolTip(str(destination.directory))

    def chosen(self) -> Destination:
        return attrs.evolve(self._destination, directory=self._directory)

    def _show_directory(self) -> None:
        self._path_label.setText(self._directory.name or str(self._directory))
        self._path_label.setToolTip(str(self._directory))

    def _on_browse(self) -> None:
        picked = QFileDialog.getExistingDirectory(
            self, "Choose a folder", str(self._directory)
        )
        if not picked:
            return
        self._directory = Path(picked)
        self._show_directory()
        save_last_custom_folder(self._directory)


class _ShotGridRow(_Row):
    """The ShotGrid peer row: a Version upload, optionally linked to a
    review playlist."""

    _upload: ShotGridUpload
    _options: QFrame
    _playlist_check: QCheckBox
    _playlist_combo: QComboBox
    _refresh_button: QPushButton
    _description: QLineEdit
    _playlists_loaded: bool

    def __init__(self, upload: ShotGridUpload) -> None:
        super().__init__(SHOTGRID_DESTINATION_NAME)
        self._upload = upload
        self._playlists_loaded = False
        self._checkbox.setToolTip(
            f"Upload a Version to {upload.entity_kind} {upload.entity_value}."
        )

        self._playlist_check = QCheckBox("Add to review playlist")
        if upload.playlist_required:
            self._playlist_check.setChecked(True)
            self._playlist_check.setToolTip(
                "Required here: these Versions are only discoverable "
                "inside a review playlist."
            )
        self._playlist_check.toggled.connect(lambda _checked: self._sync_enabled())
        self._playlist_combo = QComboBox()
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self._on_refresh)
        self._description = QLineEdit()
        self._description.setPlaceholderText("Description (optional)")

        combo_row = QHBoxLayout()
        combo_row.addWidget(self._playlist_combo, stretch=1)
        combo_row.addWidget(self._refresh_button)

        # A framed well that appears only while ShotGrid is checked
        self._options = QFrame()
        self._options.setObjectName("shotgridOptions")
        self._options.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        options_layout = QVBoxLayout(self._options)
        options_layout.addWidget(self._playlist_check)
        options_layout.addLayout(combo_row)
        options_layout.addWidget(self._description)

        indent = QHBoxLayout()
        indent.setContentsMargins(style.PAD_L, 0, 0, 0)
        indent.addWidget(self._options)
        self._column.addLayout(indent)
        self._sync_enabled()

    @property
    def playlist_id(self) -> int | None:
        if not self._playlist_check.isChecked():
            return None
        selected = self._playlist_combo.currentData()
        if isinstance(selected, int) and selected > 0:
            return selected
        return None

    @property
    def description(self) -> str | None:
        return self._description.text().strip() or None

    def validation_error(self) -> str | None:
        if self._playlist_check.isChecked() and self.playlist_id is None:
            return "Pick a review playlist for the ShotGrid upload."
        return None

    def set_delivered(self, detail: str) -> None:
        super().set_delivered(detail)
        self._sync_enabled()

    def _on_toggled(self) -> None:
        self._sync_enabled()
        super()._on_toggled()

    def _sync_enabled(self) -> None:
        # Sub-options are only relevant while the upload is selected.
        self._options.setVisible(self.is_checked)
        active = self.is_checked and self._state is not RowState.DELIVERED
        self._playlist_check.setEnabled(active and not self._upload.playlist_required)
        self._description.setEnabled(active)
        playlist_on = active and self._playlist_check.isChecked()
        self._playlist_combo.setEnabled(playlist_on)
        self._refresh_button.setEnabled(playlist_on)
        if playlist_on and not self._playlists_loaded:
            self._load_playlists()

    def _on_refresh(self) -> None:
        self._load_playlists()

    def _load_playlists(self) -> None:
        self._playlists_loaded = True
        try:
            options = list_recent_review_playlists(limit=10)
        except Exception:
            log.exception("Could not load ShotGrid review playlists")
            self._set_placeholder("Could not load reviews — Refresh to retry.")
            return
        self._playlist_combo.clear()
        if not options:
            self._set_placeholder("No recent reviews found.")
            return
        for option in options:
            label = f"{option.display_name} (#{option.playlist_id})"
            self._playlist_combo.addItem(label, option.playlist_id)

    def _set_placeholder(self, label: str) -> None:
        self._playlist_combo.clear()
        self._playlist_combo.addItem(label, None)


class _SendToEditRow(_Row):
    """The Send to Edit peer row (previs only): delivers an immutable take and
    stamps the sequence manifest. Present only when the clip carries a
    `PrevisStamp`. The take version is allocated by the engine at Confirm, so
    the row shows only its target, not a version number."""

    _stamp: PrevisStamp

    def __init__(self, stamp: PrevisStamp) -> None:
        super().__init__(SEND_TO_EDIT_DESTINATION_NAME)
        self._stamp = stamp
        self._checkbox.setToolTip(
            f"Deliver an immutable take for {stamp.shot_code} "
            f"(sequence {stamp.sequence_code}) and stamp the previs manifest."
        )


class ConfirmPanel(QWidget):
    """The Destinations checklist for one clip. Emits `state_changed` when
    its `status` may have moved, so the window can update badges, advance to
    the next pending clip, and gate closing."""

    state_changed = Signal()

    _clip: PreviewClip
    _fps: int
    _basename: str | None
    _running: bool
    _job: _ConfirmJob | None
    _edit_row: _SendToEditRow | None
    _folder_rows: list[_FolderRow]
    _sg_row: _ShotGridRow | None
    _error_label: QLabel
    _confirm_button: QPushButton

    def __init__(self, clip: PreviewClip, *, fps: int) -> None:
        super().__init__()
        self._clip = clip
        self._fps = fps
        self._basename = None
        self._running = False
        self._job = None
        self._edit_row = (
            _SendToEditRow(clip.previs_stamp) if clip.previs_stamp else None
        )
        self._folder_rows = [_FolderRow(spot) for spot in clip.destinations]
        self._sg_row = _ShotGridRow(clip.shotgrid) if clip.shotgrid else None
        if not self.is_confirmable:
            return

        destinations = QGroupBox("Destinations")
        destinations_layout = QVBoxLayout(destinations)
        destinations_layout.setSpacing(style.GAP)
        for row in self._all_rows():
            destinations_layout.addWidget(row)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(style.FAIL_STYLE)
        self._error_label.hide()
        self._confirm_button = QPushButton()
        # Confirm is the panel's primary action; the accent variant reads it as
        # the anchor the way Maya's OK button did.
        self._confirm_button.setProperty("primary", True)
        self._confirm_button.clicked.connect(self.request_confirm)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(destinations)
        column.addStretch(1)
        column.addWidget(self._error_label)
        column.addWidget(self._confirm_button)

        self._apply_remembered_toggles()
        for row in self._all_rows():
            row.toggled.connect(self._on_row_toggled)
        self._refresh_confirm_button()

    # ------------------------------------------------------------------
    # State the window reads
    # ------------------------------------------------------------------

    @property
    def is_confirmable(self) -> bool:
        return bool(self._edit_row or self._folder_rows or self._sg_row)

    @property
    def status(self) -> PanelStatus:
        if not self.is_confirmable:
            return PanelStatus.VIEW_ONLY
        if self._running:
            return PanelStatus.RUNNING
        rows = self._all_rows()
        if any(row.is_checked and row.state is RowState.FAILED for row in rows):
            return PanelStatus.FAILED
        if any(row.is_checked and row.state is RowState.IDLE for row in rows):
            return PanelStatus.PENDING
        if any(row.state is RowState.DELIVERED for row in rows):
            return PanelStatus.CONFIRMED
        return PanelStatus.PENDING

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------

    def request_confirm(self) -> None:
        """Deliver the checked-but-undelivered rows on a worker thread."""
        rows = self._rows_to_run()
        if self._running or not rows:
            return
        sg = self._sg_row if self._sg_row in rows else None
        if sg is not None:
            error = sg.validation_error()
            if error is not None:
                self._show_error(error)
                return
        run_edit = self._edit_row is not None and self._edit_row in rows

        self._show_error("")
        self._remember_toggles()
        choices = ConfirmChoices(
            destinations=tuple(
                row.chosen() for row in rows if isinstance(row, _FolderRow)
            ),
            send_to_edit=run_edit,
            upload_to_shotgrid=sg is not None,
            review_playlist_id=sg.playlist_id if sg else None,
            description=sg.description if sg else None,
        )
        for row in rows:
            row.set_running()
        self._running = True
        self._refresh_confirm_button()
        self.state_changed.emit()

        job = _ConfirmJob(
            lambda: confirm_clip(
                self._clip, choices, fps=self._fps, basename=self._basename
            )
        )
        job.signals.finished.connect(self._on_confirm_finished)
        job.signals.failed.connect(self._on_confirm_error)
        self._job = job
        QThreadPool.globalInstance().start(job)

    def _on_confirm_finished(self, result: ConfirmResult) -> None:
        self._job = None
        self._running = False
        self._basename = result.basename
        rows_by_name = {row.name: row for row in self._all_rows()}
        for outcome in result.outcomes:
            row = rows_by_name.get(outcome.name)
            if row is None:
                continue
            if outcome.ok:
                row.set_delivered(outcome.detail)
            else:
                row.set_failed(outcome.detail)
        self._refresh_confirm_button()
        self.state_changed.emit()

    def _on_confirm_error(self, reason: str) -> None:
        self._job = None
        self._running = False
        for row in self._all_rows():
            if row.state is RowState.RUNNING:
                row.set_idle()
        self._show_error(f"Could not confirm: {reason}")
        self._refresh_confirm_button()
        self.state_changed.emit()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _all_rows(self) -> list[_Row]:
        # Send to Edit leads: for a previs clip it is the primary delivery, and
        # its take version sets the shared basename the other rows reuse.
        rows: list[_Row] = []
        if self._edit_row is not None:
            rows.append(self._edit_row)
        rows.extend(self._folder_rows)
        if self._sg_row is not None:
            rows.append(self._sg_row)
        return rows

    def _rows_to_run(self) -> list[_Row]:
        return [
            row
            for row in self._all_rows()
            if row.is_checked and row.state in (RowState.IDLE, RowState.FAILED)
        ]

    def _on_row_toggled(self) -> None:
        self._refresh_confirm_button()
        self.state_changed.emit()

    def _refresh_confirm_button(self) -> None:
        rows = self._rows_to_run()
        if self._running:
            self._confirm_button.setEnabled(False)
            self._confirm_button.setText("Confirming…")
        elif rows:
            self._confirm_button.setEnabled(True)
            retrying = all(row.state is RowState.FAILED for row in rows)
            self._confirm_button.setText("Retry failed" if retrying else "Confirm")
        else:
            self._confirm_button.setEnabled(False)
            delivered = any(row.state is RowState.DELIVERED for row in self._all_rows())
            self._confirm_button.setText("Confirmed ✓" if delivered else "Confirm")
            self._confirm_button.setToolTip(
                "" if delivered else "Check at least one destination."
            )

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.setVisible(bool(message))

    def _apply_remembered_toggles(self) -> None:
        remembered = load_checked_destinations(self._clip.settings_key)
        if self._edit_row is not None:
            # Send to Edit is the point of a take export, so it defaults on.
            checked = True
            if remembered is not None:
                checked = self._edit_row.name in remembered
            self._edit_row.set_checked(checked)
        for destination, row in zip(self._clip.destinations, self._folder_rows):
            checked = destination.default_on
            if remembered is not None:
                checked = row.name in remembered
            row.set_checked(checked)
        if self._sg_row is not None:
            self._sg_row.set_checked(
                remembered is not None and self._sg_row.name in remembered
            )

    def _remember_toggles(self) -> None:
        save_checked_destinations(
            self._clip.settings_key,
            [row.name for row in self._all_rows() if row.is_checked],
        )


__all__ = ["ConfirmPanel", "PanelStatus"]
