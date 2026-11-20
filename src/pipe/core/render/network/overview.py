"""The Tractor Overview: every unfinished job on the farm, from every artist.

What is rendering now, what waits behind it, and in what order Tractor will
run them. Choosing a job shows its tasks below, with the log of the task
chosen, to read and retry, as on a shot's Farm tab; double-clicking a job of a
known shot opens it on that shot's Farm tab.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render import farm
from pipe.core.render.farm import PAUSED, RENDERING, WAITING, Job
from pipe.core.render.network import background, widgets
from pipe.core.render.network.job_tasks import JobTasks, shared_login
from pipe.core.render.network.progress import PERCENT_ROLE, ProgressDelegate

_REFRESH_MS = 30_000
_DATA_ROLE = QtCore.Qt.UserRole
_COLUMNS = (
    "Job",
    "Status",
    "Shot",
    "Title",
    "Owner",
    "Priority",
    "Progress",
    "Frames",
    "Rendering",
    "Errors",
    "Sent",
)
_STATUS, _SHOT, _PROGRESS, _ERRORS, _SENT = 1, 2, 6, 9, 10
_COLORS = {
    RENDERING: "#6ca0e0",
    WAITING: "#c9c9c9",
    PAUSED: "#c9a14a",
}
_ERROR_COLOR = "#e06c6c"


class OverviewPage(QtWidgets.QWidget):
    """`open_job` carries a shot's code and a job id, to show on its Farm tab."""

    open_job = QtCore.Signal(str, int)
    # The Refresh button was clicked: the window refreshes its other Tractor
    # pages too, so all agree.
    refresh_all = QtCore.Signal()

    _table: QtWidgets.QTreeWidget
    _tasks: JobTasks
    _summary: QtWidgets.QLabel
    _status: QtWidgets.QLabel
    _show: dict[str, QtWidgets.QCheckBox]
    _mine: QtWidgets.QCheckBox
    _shot_codes: list[str]
    _jobs: list[Job]
    _timer: QtCore.QTimer
    _user: str

    def __init__(
        self,
        shot_codes: Sequence[str],
        user: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shot_codes, self._user, self._jobs = list(shot_codes), user, []

        title = widgets.PageHeading(
            "activity", "Tractor Overview", "Every unfinished job on the farm"
        )
        self._summary = QtWidgets.QLabel()
        self._summary.setStyleSheet("font-size: 13pt;")
        self._status = QtWidgets.QLabel()
        self._status.setEnabled(False)
        refresh = widgets.button(
            "Refresh",
            "refresh",
            "Ask Tractor again now, on every Tractor page",
            clicked=self._refresh_clicked,
        )

        filters = QtWidgets.QHBoxLayout()
        self._show = {}
        for status in (RENDERING, WAITING, PAUSED):
            box = QtWidgets.QCheckBox(status)
            box.setChecked(status != PAUSED)
            box.toggled.connect(lambda _: self._fill())
            filters.addWidget(box)
            self._show[status] = box
        self._mine = QtWidgets.QCheckBox("Only mine")
        self._mine.toggled.connect(lambda _: self._fill())
        filters.addWidget(self._mine)
        filters.addStretch(1)
        filters.addWidget(self._status)
        filters.addWidget(refresh)

        self._table = QtWidgets.QTreeWidget()
        self._table.setHeaderLabels(list(_COLUMNS))
        self._table.setRootIsDecorated(False)
        self._table.setUniformRowHeights(True)
        self._table.setAlternatingRowColors(True)
        self._table.setItemDelegateForColumn(_PROGRESS, ProgressDelegate(self._table))
        self._table.itemDoubleClicked.connect(self._opened)
        self._table.currentItemChanged.connect(lambda *_: self._job_chosen())
        widgets.sortable(self._table)
        self._tasks = JobTasks(shared_login())
        self._tasks.message.connect(self._status.setText)
        self._tasks.changed.connect(
            lambda: QtCore.QTimer.singleShot(2000, self.refresh)
        )
        # R retries the chosen job's errors anywhere on this page, as on the
        # Farm tab.
        retry_key = _shortcut(QtGui.QKeySequence("R"), self)
        retry_key.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        retry_key.activated.connect(self._tasks.retry_errors_key)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(self._table)
        split.addWidget(self._tasks)
        split.setSizes([300, 420])

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._summary)
        hint = QtWidgets.QLabel(
            "In the order Tractor runs them: rendering first, then by priority, then "
            "oldest. Choose a job to see its tasks and logs below; double-click it to "
            "open it on its shot's Farm tab."
        )
        hint.setEnabled(False)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addLayout(filters)
        layout.addWidget(split, 1)
        layout.addWidget(hint)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _refresh_clicked(self) -> None:
        self.refresh()
        self.refresh_all.emit()

    def refresh(self) -> None:
        self._status.setText("Asking Tractor…")
        background.run(farm.all_jobs, self._read, self._failed)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt's name)
        super().showEvent(event)
        self.refresh()

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    def _read(self, jobs: list[Job]) -> None:
        self._jobs = farm.queue_order([job for job in jobs if not job.finished])
        self._fill()
        rendering = [j for j in self._jobs if j.queue_status == RENDERING]
        waiting = [j for j in self._jobs if j.queue_status == WAITING]
        frames = sum(j.active for j in rendering)
        self._summary.setText(
            f"<span style='color:{_COLORS[RENDERING]}'>{len(rendering)} rendering</span>"
            f" ({frames} frames) · {len(waiting)} waiting"
        )
        self._status.setText(f"updated {time.strftime('%H:%M:%S')}")

    def _fill(self) -> None:
        chosen = {item.data(0, _DATA_ROLE).jid for item in self._table.selectedItems()}
        shown = self._tasks.job
        self._table.blockSignals(True)
        self._table.clear()
        current = None
        for job in self._jobs:
            if not self._show[job.queue_status].isChecked():
                continue
            if self._mine.isChecked() and job.owner != self._user:
                continue
            shot = farm.shot_of(job, self._shot_codes)
            sent = job.spooled.strftime("%b %d %H:%M") if job.spooled else ""
            item = widgets.SortableItem(
                [
                    str(job.jid),
                    job.queue_status,
                    shot or "—",
                    job.title,
                    job.owner,
                    f"{job.priority:g}",
                    "",
                    f"{job.done}/{job.total}",
                    str(job.active or ""),
                    str(job.errors or ""),
                    sent,
                ]
            )
            item.setData(0, _DATA_ROLE, job)
            item.setData(_PROGRESS, PERCENT_ROLE, job.percent)
            item.setData(_PROGRESS, widgets.SORT_ROLE, job.percent)
            item.setData(_SENT, widgets.SORT_ROLE, _when(job))
            item.setForeground(
                _STATUS, QtGui.QBrush(QtGui.QColor(_COLORS[job.queue_status]))
            )
            if job.errors:
                item.setForeground(_ERRORS, QtGui.QBrush(QtGui.QColor(_ERROR_COLOR)))
            if shot is None:
                item.setToolTip(
                    _SHOT, "Its shot isn't known, so it can't be opened here"
                )
            self._table.addTopLevelItem(item)
            item.setSelected(job.jid in chosen)
            if shown is not None and job.jid == shown.jid:
                current = item
        if current is not None:
            self._table.setCurrentItem(current, 0, QtCore.QItemSelectionModel.NoUpdate)
        self._table.blockSignals(False)
        if current is not None:
            # The open job's tasks too, keeping the artist's place.
            self._tasks.show_job(current.data(0, _DATA_ROLE), keep_log=True)
        for column in range(len(_COLUMNS)):
            self._table.resizeColumnToContents(column)
        self._table.setColumnWidth(_PROGRESS, 110)

    def _job_chosen(self) -> None:
        item = self._table.currentItem()
        if item is not None:
            self._tasks.show_job(item.data(0, _DATA_ROLE))

    def _opened(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        job: Job = item.data(0, _DATA_ROLE)
        shot = farm.shot_of(job, self._shot_codes)
        if shot is None:
            self._status.setText(
                f"Job {job.jid} names no shot this window knows, so it can't be opened here."
            )
            return
        self.open_job.emit(shot, job.jid)

    def _failed(self, exc: Exception) -> None:
        self._status.setText(str(exc))


def _when(job: Job) -> float:
    """When the job was sent, to sort by; unknown ones sort first."""
    return job.spooled.timestamp() if job.spooled else 0.0


# Qt 6 moved QShortcut from QtWidgets to QtGui.
_shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
