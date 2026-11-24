"""The Tractor Overview: every unfinished job on the farm, from every artist.

What is rendering now, what waits behind it, and in what order Tractor will
run them. Double-clicking a job of a known shot opens it on that shot's Farm
tab, to read its logs or retry it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from Qt import QtCore, QtGui, QtWidgets

from pipe.core import tractor
from pipe.core.tractor import PAUSED, RENDERING, WAITING, Job
from pipe.core.render.app import background
from pipe.core.render.app.progress import PERCENT_ROLE, ProgressDelegate

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
_STATUS, _SHOT, _PROGRESS, _ERRORS = 1, 2, 6, 9
_COLORS = {
    RENDERING: "#6ca0e0",
    WAITING: "#c9c9c9",
    PAUSED: "#c9a14a",
}
_ERROR_COLOR = "#e06c6c"


class OverviewPage(QtWidgets.QWidget):
    """`open_job` carries a shot's code and a job id, to show on its Farm tab."""

    open_job = QtCore.Signal(str, int)

    _table: QtWidgets.QTreeWidget
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

        title = QtWidgets.QLabel(
            "<b>Tractor Overview</b> — every unfinished job on the farm"
        )
        self._summary = QtWidgets.QLabel()
        self._summary.setStyleSheet("font-size: 13pt;")
        self._status = QtWidgets.QLabel()
        self._status.setEnabled(False)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)

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

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._summary)
        hint = QtWidgets.QLabel(
            "In the order Tractor runs them: rendering first, then by priority, then "
            "oldest. Double-click a job to open it on its shot's Farm tab."
        )
        hint.setEnabled(False)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addLayout(header)
        layout.addLayout(filters)
        layout.addWidget(self._table, 1)
        layout.addWidget(hint)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def refresh(self) -> None:
        self._status.setText("Asking Tractor…")
        background.run(tractor.all_jobs, self._read, self._failed)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt's name)
        super().showEvent(event)
        self.refresh()

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    def _read(self, jobs: list[Job]) -> None:
        self._jobs = tractor.queue_order([job for job in jobs if not job.finished])
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
        self._table.clear()
        for job in self._jobs:
            if not self._show[job.queue_status].isChecked():
                continue
            if self._mine.isChecked() and job.owner != self._user:
                continue
            shot = tractor.shot_of(job, self._shot_codes)
            sent = job.spooled.strftime("%b %d %H:%M") if job.spooled else ""
            item = QtWidgets.QTreeWidgetItem(
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
        for column in range(len(_COLUMNS)):
            self._table.resizeColumnToContents(column)
        self._table.setColumnWidth(_PROGRESS, 110)

    def _opened(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        job: Job = item.data(0, _DATA_ROLE)
        shot = tractor.shot_of(job, self._shot_codes)
        if shot is None:
            self._status.setText(
                f"Job {job.jid} names no shot this window knows, so it can't be opened here."
            )
            return
        self.open_job.emit(shot, job.jid)

    def _failed(self, exc: Exception) -> None:
        self._status.setText(str(exc))
