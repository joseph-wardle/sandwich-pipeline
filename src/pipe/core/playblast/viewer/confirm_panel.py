from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path
from typing import assert_never

from Qt.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from Qt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
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
    ShotGridDestination,
)
from pipe.core.playblast.confirm import (
    ChosenDestination,
    ChosenDisk,
    ChosenShotGrid,
    ConfirmResult,
    confirm_clip,
    failure_summary,
)
from pipe.core.playblast.errors import artist_reason
from pipe.core.playblast.viewer import style
from pipe.core.playblast.viewer.playlists import ReviewPlaylists
from pipe.core.playblast.viewer.settings import (
    load_checked_destinations,
    load_last_custom_folder,
    save_checked_destinations,
    save_last_custom_folder,
)
from pipe.core.ui import FAIL_STYLE, OK_STYLE

log = logging.getLogger(__name__)


class RowState(Enum):
    IDLE = auto()
    RUNNING = auto()
    DELIVERED = auto()
    FAILED = auto()


class PanelStatus(Enum):
    SKIPPED = auto()
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
            self.signals.failed.emit(artist_reason(exc))


class _Row(QWidget):
    toggled = Signal()

    destination_id: DestinationId
    default_on: bool
    _state: RowState
    _unavailable: str
    _checkbox: QCheckBox
    _status: QLabel
    _detail: QLabel
    _header: QHBoxLayout
    _column: QVBoxLayout
    _options: QFrame | None
    _collapse_options: bool

    def __init__(self, destination: Destination) -> None:
        super().__init__()
        unavailable = destination.unavailable
        self.destination_id = destination.id
        # A row nobody can deliver to never starts checked, whatever it declared.
        self.default_on = destination.default_on and not unavailable
        self._state = RowState.IDLE
        self._unavailable = unavailable
        self._options = None
        self._collapse_options = True
        self._checkbox = QCheckBox(destination.name)
        # Nothing here but the text fields takes focus: a control left focused
        # by the last click would swallow the window's Space and arrow keys.
        self._checkbox.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._checkbox.toggled.connect(lambda _checked: self._on_toggled())
        self._status = QLabel("")
        self._status.setFixedWidth(style.STATUS_GLYPH_W)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        # Its text comes from exceptions and ShotGrid, which can carry angle
        # brackets Qt would otherwise read as markup.
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        # Selectable so the artist can lift a delivered path out of the panel.
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._detail.hide()

        self._header = QHBoxLayout()
        self._header.addWidget(self._checkbox)
        self._header.addStretch(1)
        if unavailable:
            self._checkbox.setEnabled(False)
            note = QLabel(unavailable)
            # Disabled rather than coloured: the viewer takes its look from the
            # host DCC, and a row that is merely not ready yet is not a failure.
            note.setEnabled(False)
            self._header.addWidget(note)
        self._header.addWidget(self._status)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.addLayout(self._header)
        self._column.addWidget(self._detail)

    def add_options(self, layout: QLayout, *, collapse: bool = True) -> None:
        """Attach this row's sub-options as an indented, framed well below the checkbox."""
        self._collapse_options = collapse
        self._options = QFrame()
        self._options.setFrameShape(QFrame.Shape.StyledPanel)
        self._options.setLayout(layout)
        indent = QHBoxLayout()
        indent.setContentsMargins(style.PAD_L, 0, 0, 0)
        indent.addWidget(self._options)
        self._column.addLayout(indent)
        self._options.setVisible(self.is_checked or not collapse)

    def _on_toggled(self) -> None:
        if self._options is not None and self._collapse_options:
            self._options.setVisible(self.is_checked)
        # Unchecking abandons the attempt, so its ✗ goes with it — otherwise the
        # row keeps reporting a failure nobody is retrying.
        if not self.is_checked and self._state is RowState.FAILED:
            self.set_idle()
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
    def is_available(self) -> bool:
        return not self._unavailable

    @property
    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        if self._unavailable:
            return
        self._checkbox.setChecked(checked)

    def _set_status(self, glyph: str, css: str) -> None:
        # Glyph and color move together, so no state inherits an earlier color.
        self._status.setText(glyph)
        self._status.setStyleSheet(css)

    def _set_detail(self, text: str, css: str, tooltip: str = "") -> None:
        self._detail.setText(text)
        self._detail.setStyleSheet(css)
        self._detail.setToolTip(tooltip)
        self._detail.setVisible(bool(text))

    # Each setter fixes the glyph, the outcome line and whether the artist can
    # still toggle the row, so none of the three can survive into a later state.

    def set_running(self) -> None:
        self._state = RowState.RUNNING
        self._set_status("…", "")
        self._set_detail("", "")
        self._checkbox.setEnabled(False)

    def set_idle(self) -> None:
        self._state = RowState.IDLE
        self._set_status("", "")
        self._set_detail("", "")
        self._checkbox.setEnabled(not self._unavailable)

    def set_delivered(self, detail: str, path: Path | None) -> None:
        self._state = RowState.DELIVERED
        self._set_status("✓", OK_STYLE)
        self._set_detail(detail, OK_STYLE, str(path) if path else "")
        self._checkbox.setEnabled(False)
        # What was delivered can't be re-aimed after the fact.
        if self._options is not None:
            self._options.setEnabled(False)

    def set_failed(self, detail: str) -> None:
        self._state = RowState.FAILED
        self._set_status("✗", FAIL_STYLE)
        self._set_detail(detail, FAIL_STYLE)
        self._checkbox.setEnabled(not self._unavailable)


class _FolderRow(_Row):
    """A disk-folder destination, showing the folder it writes into."""

    _destination: DiskDestination
    _directory: Path
    _path_label: QLabel

    def __init__(self, destination: DiskDestination) -> None:
        super().__init__(destination)
        self._destination = destination
        self._directory = destination.directory
        if destination.browsable:
            self._directory = load_last_custom_folder() or destination.directory
        if not destination.unavailable:
            self._build_options(browsable=destination.browsable)

    def _build_options(self, *, browsable: bool) -> None:
        self._path_label = QLabel()
        self._path_label.setTextFormat(Qt.TextFormat.PlainText)
        self._show_directory()
        row = QHBoxLayout()
        row.addWidget(self._path_label, stretch=1)
        if browsable:
            browse = QPushButton("Browse…")
            browse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            browse.clicked.connect(self._on_browse)
            row.addWidget(browse)
        self.add_options(row, collapse=False)

    @property
    def directory(self) -> Path:
        return self._directory

    def chosen(self) -> ChosenDisk:
        return ChosenDisk(destination=self._destination, directory=self._directory)

    def _show_directory(self) -> None:
        self._path_label.setText(_elided_path(self._directory))
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


_PATH_TAIL_PARTS = 4


def _elided_path(directory: Path) -> str:
    parts = directory.parts
    if len(parts) <= _PATH_TAIL_PARTS:
        return str(directory)
    return "…/" + "/".join(parts[-_PATH_TAIL_PARTS:])


class _ShotGridRow(_Row):
    """The ShotGrid peer row: a Version upload, optionally linked to a
    review playlist."""

    _destination: ShotGridDestination
    _playlists: ReviewPlaylists
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
        self._playlist_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._playlist_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._playlist_combo.activated.connect(lambda _index: self._remember_picked())
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_button.clicked.connect(self._on_refresh)
        self._description = QLineEdit()
        self._description.setPlaceholderText("Description (optional)")

        search_row = QHBoxLayout()
        search_row.addWidget(self._search_field, stretch=1)
        search_row.addWidget(self._refresh_button)

        options_layout = QVBoxLayout()
        options_layout.addWidget(self._playlist_check)
        options_layout.addLayout(search_row)
        options_layout.addWidget(self._playlist_combo)
        options_layout.addWidget(self._description)
        self.add_options(options_layout)

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
            # Why it failed is on the combo, next to the Refresh that retries it.
            return (
                "Could not load review playlists — press Refresh to retry. "
                f"{self._skip_hint()}"
            )
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
        """Keep the playlist controls in step with the checkbox above them.
        Unchecked and delivered need no handling here."""
        self._playlist_check.setEnabled(not self._destination.playlist_required)
        picking = self._playlist_check.isChecked()
        self._search_field.setEnabled(picking)
        self._playlist_combo.setEnabled(picking)
        self._refresh_button.setEnabled(picking)
        if picking and self.is_checked:
            self._playlists.ensure_loaded()

    def _on_refresh(self) -> None:
        """Refresh doubles as the filter's apply button."""
        self._playlists.load(self._search_field.text())

    def _show_playlists(self) -> None:
        # The filter is window-wide, so every row mirrors it.
        if self._search_field.text() != self._playlists.search:
            self._search_field.setText(self._playlists.search)
        self._playlist_combo.clear()
        self._playlist_combo.setToolTip(self._playlists.error or "")
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


def _build_row(destination: Destination, playlists: ReviewPlaylists) -> _Row:
    if isinstance(destination, DiskDestination):
        return _FolderRow(destination)
    if isinstance(destination, ShotGridDestination):
        return _ShotGridRow(destination, playlists)
    assert_never(destination)


class ConfirmPanel(QWidget):
    """The Destinations checklist for one clip."""

    state_changed = Signal()
    # Every checked destination landed — the window's cue to move on.
    delivered = Signal()

    _clip: PreviewClip
    _pool: QThreadPool
    _basename: str | None
    _job: _ConfirmJob | None
    _rows: list[_Row]
    _blocked: list[str]
    _error_label: QLabel
    _confirm_button: QPushButton

    def __init__(
        self, clip: PreviewClip, pool: QThreadPool, playlists: ReviewPlaylists
    ) -> None:
        super().__init__()
        self._clip = clip
        self._pool = pool
        self._basename = None
        self._job = None
        self._blocked = []
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setTextFormat(Qt.TextFormat.PlainText)
        self._error_label.setStyleSheet(FAIL_STYLE)
        self._error_label.hide()
        self._confirm_button = QPushButton()
        self._confirm_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        if self._running:
            return PanelStatus.RUNNING
        if any(row.state is RowState.FAILED for row in self._rows):
            return PanelStatus.FAILED
        if self._rows_to_run():
            return PanelStatus.PENDING
        if any(row.state is RowState.DELIVERED for row in self._rows):
            return PanelStatus.CONFIRMED
        return PanelStatus.SKIPPED

    @property
    def _running(self) -> bool:
        """A job is in flight — the panel holds it until it reports back."""
        return self._job is not None

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

        self._blocked = blocked
        self._report()
        if not runnable:
            return

        self._remember_toggles()
        chosen = tuple(row.chosen() for row in runnable)
        folders = tuple(
            row.directory
            for row in self._rows
            if isinstance(row, _FolderRow) and row.is_available
        )
        for row in runnable:
            row.set_running()

        job = _ConfirmJob(
            lambda: confirm_clip(self._clip, chosen, folders, basename=self._basename)
        )
        job.signals.finished.connect(self._on_confirm_finished)
        job.signals.failed.connect(self._on_confirm_error)
        self._job = job
        self._refresh_confirm_button()
        self.state_changed.emit()
        self._pool.start(job)

    def _on_confirm_finished(self, result: ConfirmResult) -> None:
        self._job = None
        self._basename = result.basename
        rows_by_id = {row.destination_id: row for row in self._rows}
        for outcome in result.outcomes:
            row = rows_by_id.get(outcome.id)
            if row is None:
                continue
            if outcome.ok:
                row.set_delivered(outcome.detail, outcome.path)
            else:
                row.set_failed(outcome.detail)
        self._report(failure_summary(result))
        self._refresh_confirm_button()
        self.state_changed.emit()
        if self.status is PanelStatus.CONFIRMED:
            self.delivered.emit()

    def _on_confirm_error(self, reason: str) -> None:
        # `confirm_clip` reports each destination's own failure, so an
        # exception escaping it means nothing was attempted.
        self._job = None
        for row in self._rows:
            if row.state is RowState.RUNNING:
                row.set_idle()
        self._report(f"Nothing was delivered. {reason}")
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
        # A toggle may have answered a blocked reason; the next Confirm
        # recomputes whichever still hold.
        self._blocked = []
        self._report()
        self._refresh_confirm_button()
        self.state_changed.emit()

    def _refresh_confirm_button(self) -> None:
        rows = self._rows_to_run()
        if self._running:
            self._set_confirm_button(
                "Confirming…", "This clip is being delivered.", enabled=False
            )
            return
        if rows:
            retrying = all(row.state is RowState.FAILED for row in rows)
            action = (
                "Try the destinations that failed again."
                if retrying
                else "Deliver this playblast to every checked destination."
            )
            self._set_confirm_button(
                "Retry failed" if retrying else "Confirm",
                f"{action} (Ctrl+Enter)",
                enabled=True,
            )
            return
        delivered = any(row.state is RowState.DELIVERED for row in self._rows)
        self._set_confirm_button(
            "Confirmed ✓" if delivered else "Confirm",
            "Every checked destination has been delivered."
            if delivered
            else "Check at least one destination first.",
            enabled=False,
        )

    def _set_confirm_button(self, text: str, tooltip: str, *, enabled: bool) -> None:
        # Set together, so an enabled button cannot keep the tooltip that
        # explained why it was disabled.
        self._confirm_button.setText(text)
        self._confirm_button.setToolTip(tooltip)
        self._confirm_button.setEnabled(enabled)

    def _report(self, headline: str = "") -> None:
        """Show `headline` over the reasons rows were blocked, if any.

        `_blocked` outlives the Confirm that set it, so a result reporting on
        the rows that *did* run cannot erase the rows that could not.
        """
        lines = [line for line in (headline, *self._blocked) if line]
        self._error_label.setText("\n".join(lines))
        self._error_label.setVisible(bool(lines))

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
