from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path

from Qt.QtCore import QObject, QRunnable, QThreadPool, Signal
from Qt.QtWidgets import (
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

from pipe.core.playblast.clip import (
    Destination,
    DestinationId,
    DiskDestination,
    PreviewClip,
    PrevisTakeDestination,
    ShotGridDestination,
)
from pipe.core.playblast.confirm import (
    ChosenDestination,
    ChosenDisk,
    ChosenShotGrid,
    ChosenTake,
    ConfirmResult,
    confirm_clip,
)
from pipe.core.playblast.viewer import style
from pipe.core.playblast.viewer.playlists import ReviewPlaylists
from pipe.core.playblast.viewer.settings import (
    load_checked_destinations,
    load_last_custom_folder,
    save_checked_destinations,
    save_last_custom_folder,
)

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
    toggled = Signal()

    destination_id: DestinationId
    default_on: bool
    _state: RowState
    _checkbox: QCheckBox
    _status: QLabel
    _detail: QLabel
    _header: QHBoxLayout
    _column: QVBoxLayout

    def __init__(self, destination: Destination) -> None:
        super().__init__()
        self.destination_id = destination.id
        self.default_on = destination.default_on
        self._state = RowState.IDLE
        self._checkbox = QCheckBox(destination.name)
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

    def chosen(self) -> ChosenDestination:
        raise NotImplementedError

    def validation_error(self) -> str | None:
        """An artist-facing reason this row cannot be confirmed yet."""
        return None

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

    _destination: DiskDestination
    _directory: Path
    _options: QFrame | None
    _path_label: QLabel

    def __init__(self, destination: DiskDestination) -> None:
        super().__init__(destination)
        self._destination = destination
        self._directory = destination.directory
        self._options = None
        if destination.browsable:
            self._directory = load_last_custom_folder() or destination.directory
            self._build_options()
        else:
            self._checkbox.setToolTip(str(destination.directory))

    def _build_options(self) -> None:
        self._path_label = QLabel()
        self._show_directory()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        self._options = QFrame()
        self._options.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(self._options)
        row.addWidget(self._path_label, stretch=1)
        row.addWidget(browse)
        indent = QHBoxLayout()
        indent.setContentsMargins(style.PAD_L, 0, 0, 0)
        indent.addWidget(self._options)
        self._column.addLayout(indent)
        self._options.setVisible(self.is_checked)

    def chosen(self) -> ChosenDisk:
        return ChosenDisk(destination=self._destination, directory=self._directory)

    def set_delivered(self, detail: str) -> None:
        super().set_delivered(detail)
        # the folder can't be changed after the fact.
        if self._options is not None:
            self._options.setEnabled(False)

    def _on_toggled(self) -> None:
        if self._options is not None:
            self._options.setVisible(self.is_checked)
        super()._on_toggled()

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

    _destination: ShotGridDestination
    _playlists: ReviewPlaylists
    _options: QFrame
    _playlist_check: QCheckBox
    _search_field: QLineEdit
    _playlist_combo: QComboBox
    _refresh_button: QPushButton
    _description: QLineEdit
    _picked: tuple[int, str] | None

    def __init__(
        self, destination: ShotGridDestination, playlists: ReviewPlaylists
    ) -> None:
        super().__init__(destination)
        self._destination = destination
        self._playlists = playlists
        self._picked = None
        self._checkbox.setToolTip(
            f"Upload a Version to {destination.entity.description}."
        )

        self._playlist_check = QCheckBox("Add to review playlist")
        if destination.playlist_required:
            self._playlist_check.setChecked(True)
            self._playlist_check.setToolTip(
                "Required here: these Versions are only discoverable "
                "inside a review playlist."
            )
        self._playlist_check.toggled.connect(lambda _checked: self._sync_enabled())
        self._search_field = QLineEdit()
        self._search_field.setPlaceholderText("Filter playlists by name (press Enter)")
        self._search_field.returnPressed.connect(self._on_refresh)
        self._playlist_combo = QComboBox()
        self._playlist_combo.activated.connect(lambda _index: self._remember_picked())
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self._on_refresh)
        self._description = QLineEdit()
        self._description.setPlaceholderText("Description (optional)")

        search_row = QHBoxLayout()
        search_row.addWidget(self._search_field, stretch=1)
        search_row.addWidget(self._refresh_button)

        # A framed well that appears only while ShotGrid is checked
        self._options = QFrame()
        self._options.setFrameShape(QFrame.Shape.StyledPanel)
        options_layout = QVBoxLayout(self._options)
        options_layout.addWidget(self._playlist_check)
        options_layout.addLayout(search_row)
        options_layout.addWidget(self._playlist_combo)
        options_layout.addWidget(self._description)

        indent = QHBoxLayout()
        indent.setContentsMargins(style.PAD_L, 0, 0, 0)
        indent.addWidget(self._options)
        self._column.addLayout(indent)
        self._playlists.changed.connect(self._show_playlists)
        self._show_playlists()
        self._sync_enabled()

    def chosen(self) -> ChosenShotGrid:
        return ChosenShotGrid(
            destination=self._destination,
            playlist_id=self._playlist_id,
            description=self._description.text().strip() or None,
        )

    def validation_error(self) -> str | None:
        if not self._playlist_check.isChecked() or self._playlist_id is not None:
            return None
        if not self._playlists.settled:
            return "Still loading review playlists — try Confirm again in a moment."
        if self._playlists.error is not None:
            return f"Could not load review playlists — press Refresh to retry. {self._skip_hint()}"
        if not self._playlists.options:
            if self._playlists.search:
                return (
                    f"No review playlist matches '{self._playlists.search}'. Clear "
                    "the filter and press Refresh."
                )
            return f"ShotGrid has no review playlists yet. {self._skip_hint()}"
        return "Pick a review playlist for the ShotGrid upload."

    def _skip_hint(self) -> str:
        """A playlist the artist cannot pick leaves this row stuck, so every
        unpickable case has to name the way past it."""
        if self._destination.playlist_required:
            return (
                "Uncheck ShotGrid to skip the upload — a Version outside a "
                "playlist would be undiscoverable."
            )
        return "Uncheck 'Add to review playlist' to upload without one."

    def set_delivered(self, detail: str) -> None:
        super().set_delivered(detail)
        self._sync_enabled()

    @property
    def _playlist_id(self) -> int | None:
        if not self._playlist_check.isChecked():
            return None
        selected = self._playlist_combo.currentData()
        if isinstance(selected, int) and selected > 0:
            return selected
        return None

    def _on_toggled(self) -> None:
        self._sync_enabled()
        super()._on_toggled()

    def _sync_enabled(self) -> None:
        # Sub-options are only relevant while the upload is selected.
        self._options.setVisible(self.is_checked)
        active = self.is_checked and self._state is not RowState.DELIVERED
        self._playlist_check.setEnabled(
            active and not self._destination.playlist_required
        )
        self._description.setEnabled(active)
        playlist_on = active and self._playlist_check.isChecked()
        self._search_field.setEnabled(playlist_on)
        self._playlist_combo.setEnabled(playlist_on)
        self._refresh_button.setEnabled(playlist_on)
        if playlist_on:
            self._playlists.ensure_loaded()

    def _on_refresh(self) -> None:
        """Refresh doubles as the filter's apply button."""
        self._playlists.load(self._search_field.text())

    def _show_playlists(self) -> None:
        # The filter is window-wide, so every row mirrors it.
        if self._search_field.text() != self._playlists.search:
            self._search_field.setText(self._playlists.search)
        self._playlist_combo.clear()
        if not self._playlists.settled:
            self._playlist_combo.addItem("Loading reviews…", None)
            return
        if self._playlists.error is not None:
            self._playlist_combo.addItem(
                "Could not load reviews — Refresh to retry.", None
            )
            return
        for option in self._playlists.options:
            self._playlist_combo.addItem(
                f"{option.display_name} (#{option.playlist_id})", option.playlist_id
            )
        self._restore_picked()
        if not self._playlist_combo.count():
            self._playlist_combo.addItem(self._empty_text(), None)

    def _remember_picked(self) -> None:
        """Only a pick the artist made themselves, so a repopulated combo
        auto-selecting its first row is never mistaken for one."""
        picked = self._playlist_combo.currentData()
        if isinstance(picked, int):
            self._picked = (picked, self._playlist_combo.currentText())

    def _restore_picked(self) -> None:
        """Neither a Refresh nor a filter may silently repoint an already-picked
        playlist, so one that falls outside the new results is re-added."""
        if self._picked is None:
            return
        picked_id, label = self._picked
        index = self._playlist_combo.findData(picked_id)
        if index < 0:
            self._playlist_combo.insertItem(0, label, picked_id)
            index = 0
        self._playlist_combo.setCurrentIndex(index)

    def _empty_text(self) -> str:
        if self._playlists.search:
            return f"No playlist matches '{self._playlists.search}'."
        return "No review playlists found."


class _SendToEditRow(_Row):
    """The Send to Edit peer row (previs only): delivers an immutable take and
    stamps the sequence manifest."""

    _destination: PrevisTakeDestination

    def __init__(self, destination: PrevisTakeDestination) -> None:
        super().__init__(destination)
        self._destination = destination
        stamp = destination.stamp
        self._checkbox.setToolTip(
            f"Deliver an immutable take for {stamp.shot_code} "
            f"(sequence {stamp.sequence_code}) and stamp the previs manifest."
        )

    def chosen(self) -> ChosenTake:
        return ChosenTake(destination=self._destination)


def _build_row(destination: Destination, playlists: ReviewPlaylists) -> _Row:
    if isinstance(destination, DiskDestination):
        return _FolderRow(destination)
    if isinstance(destination, ShotGridDestination):
        return _ShotGridRow(destination, playlists)
    return _SendToEditRow(destination)


class ConfirmPanel(QWidget):
    """The Destinations checklist for one clip."""

    state_changed = Signal()

    _clip: PreviewClip
    _pool: QThreadPool
    _basename: str | None
    _running: bool
    _job: _ConfirmJob | None
    _rows: list[_Row]
    _error_label: QLabel
    _confirm_button: QPushButton

    def __init__(
        self, clip: PreviewClip, pool: QThreadPool, playlists: ReviewPlaylists
    ) -> None:
        super().__init__()
        self._clip = clip
        self._pool = pool
        self._basename = None
        self._running = False
        self._job = None
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(style.FAIL_STYLE)
        self._error_label.hide()
        self._confirm_button = QPushButton()
        self._confirm_button.clicked.connect(self.request_confirm)

        self._rows = [
            _build_row(destination, playlists) for destination in clip.destinations
        ]
        if not self.is_confirmable:
            return

        destinations = QGroupBox("Destinations")
        destinations_layout = QVBoxLayout(destinations)
        destinations_layout.setSpacing(style.GAP)
        for row in self._rows:
            destinations_layout.addWidget(row)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(destinations)
        column.addStretch(1)
        column.addWidget(self._error_label)
        column.addWidget(self._confirm_button)

        self._apply_remembered_toggles()
        for row in self._rows:
            row.toggled.connect(self._on_row_toggled)
        self._refresh_confirm_button()

    # ------------------------------------------------------------------
    # State the window reads
    # ------------------------------------------------------------------

    @property
    def is_confirmable(self) -> bool:
        return bool(self._rows)

    @property
    def status(self) -> PanelStatus:
        if not self.is_confirmable:
            return PanelStatus.VIEW_ONLY
        if self._running:
            return PanelStatus.RUNNING
        if any(row.is_checked and row.state is RowState.FAILED for row in self._rows):
            return PanelStatus.FAILED
        if any(row.is_checked and row.state is RowState.IDLE for row in self._rows):
            return PanelStatus.PENDING
        if any(row.state is RowState.DELIVERED for row in self._rows):
            return PanelStatus.CONFIRMED
        return PanelStatus.PENDING

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------

    def request_confirm(self) -> None:
        """Deliver every checked-but-undelivered row that validates. A row that
        does not stays checked and idle, so Confirm remains live to retry it."""
        if self._running:
            return
        rows = self._rows_to_run()
        if not rows:
            return
        runnable: list[_Row] = []
        blocked: list[str] = []
        for row in rows:
            error = row.validation_error()
            if error is None:
                runnable.append(row)
            else:
                blocked.append(error)

        self._show_error("\n".join(blocked))
        if not runnable:
            return

        self._remember_toggles()
        chosen = tuple(row.chosen() for row in runnable)
        for row in runnable:
            row.set_running()
        self._running = True
        self._refresh_confirm_button()
        self.state_changed.emit()

        job = _ConfirmJob(
            lambda: confirm_clip(self._clip, chosen, basename=self._basename)
        )
        job.signals.finished.connect(self._on_confirm_finished)
        job.signals.failed.connect(self._on_confirm_error)
        self._job = job
        self._pool.start(job)

    def _on_confirm_finished(self, result: ConfirmResult) -> None:
        self._job = None
        self._running = False
        self._basename = result.basename
        rows_by_id = {row.destination_id: row for row in self._rows}
        for outcome in result.outcomes:
            row = rows_by_id.get(outcome.id)
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
        for row in self._rows:
            if row.state is RowState.RUNNING:
                row.set_idle()
        self._show_error(f"Could not confirm: {reason}")
        self._refresh_confirm_button()
        self.state_changed.emit()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rows_to_run(self) -> list[_Row]:
        return [
            row
            for row in self._rows
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
            delivered = any(row.state is RowState.DELIVERED for row in self._rows)
            self._confirm_button.setText("Confirmed ✓" if delivered else "Confirm")
            self._confirm_button.setToolTip(
                "" if delivered else "Check at least one destination."
            )

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.setVisible(bool(message))

    def _apply_remembered_toggles(self) -> None:
        remembered = load_checked_destinations(self._clip.settings_key)
        for row in self._rows:
            checked = row.default_on
            if remembered is not None:
                checked = row.destination_id in remembered
            row.set_checked(checked)

    def _remember_toggles(self) -> None:
        save_checked_destinations(
            self._clip.settings_key,
            [row.destination_id for row in self._rows if row.is_checked],
        )


__all__ = ["ConfirmPanel", "PanelStatus"]
