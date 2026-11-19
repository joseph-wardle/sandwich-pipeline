"""The Farm tab: every Tractor job for the shot, its tasks and their logs, and
retrying what errored.

Each render task shows the size of the EXR it wrote, flagged when it is far
from the frames around it, and Size Graph draws them across the shot.

The farm already reruns a render frame by itself when it crashes, runs out of
memory or misses a license (`job.RENDER_RETRIES`); what errors past that is
listed here to read and retry by hand.
"""

from __future__ import annotations

import time
from pathlib import Path

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render import farm
from pipe.core.render.farm import (
    ERROR,
    PAUSED,
    RENDERING,
    WAITING,
    FarmError,
    Job,
    LoginRefused,
)
from pipe.core.render.network import background, theme, widgets
from pipe.core.render.network.job_tasks import DIM, GREY, GREEN, RED, JobTasks
from pipe.core.render.network.job_tasks import shared_login
from pipe.core.render.network.size_graph import SizeGraphDialog

_DATA_ROLE = QtCore.Qt.UserRole
_REFRESH_MS = 30_000
# The jobs table's Sent column.
_SENT = 3
_RENDER_TASK = farm.RENDER_TASK
_RED, _GREEN, _GREY, _DIM = RED, GREEN, GREY, DIM
# A job's whole row: (text, background or None). Errored and active jobs
# stand out; done and waiting ones step back.
_JOB_ROWS: dict[str, tuple[str, str | None]] = {
    ERROR: (_RED, "#40252a"),
    RENDERING: (_GREEN, "#1f3a28"),
    PAUSED: ("#c9a14a", None),
    WAITING: (_GREY, None),
    "Done": (_DIM, None),
}


class FarmTab(QtWidgets.QWidget):
    """`view_frames` asks the Images tab for a layer, its folder or None, and a frame or None."""

    view_frames = QtCore.Signal(str, object, object)

    _jobs: QtWidgets.QTreeWidget
    _tasks: JobTasks
    _status: QtWidgets.QLabel
    _archive_button: QtWidgets.QPushButton
    _forget: QtWidgets.QPushButton
    _view: QtWidgets.QPushButton
    _graph: QtWidgets.QPushButton
    _timer: QtCore.QTimer

    _shot: str | None
    # What the last action did, shown once with the next refreshed list.
    _note: str

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._shot = None
        self._login = shared_login()
        self._login.changed.connect(self._show_login)
        self._login.note.connect(self._noted)
        self._wanted_jid: int | None = None
        self._note = ""

        self._jobs = _table(
            ["Job", "Title", "Owner", "Sent", "Frames", "Errors", "Status"]
        )
        self._jobs.currentItemChanged.connect(lambda *_: self._job_chosen())
        self._jobs.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._jobs.itemSelectionChanged.connect(self._enable)
        self._tasks = JobTasks(self._login)
        self._tasks.message.connect(self._said)
        self._tasks.changed.connect(
            lambda: QtCore.QTimer.singleShot(2000, self.refresh)
        )
        self._tasks.loaded.connect(self._enable)
        self._tasks.activated.connect(self._on_view)

        self._forget = widgets.button(
            "Forget Login",
            "log-out",
            "Remove the Tractor login kept in your keyring",
            theme.QUIET,
            self._forget_login,
        )
        self._archive_button = widgets.button(
            "Archive",
            "archive",
            "Move the selected jobs to Tractor's archive. Ctrl- or Shift-click to "
            "choose several.",
            clicked=self._archive,
        )
        refresh = widgets.button(
            "Refresh", "refresh", "Ask Tractor again now", clicked=self.refresh
        )
        self._status = QtWidgets.QLabel()
        self._status.setEnabled(False)
        # Anywhere in the Farm tab, and nowhere else in the window.
        retry_key = _shortcut(QtGui.QKeySequence("R"), self)
        retry_key.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        retry_key.activated.connect(self._tasks.retry_errors_key)
        self._view = widgets.button(
            "View Frames",
            "eye",
            "Show this task's frame in the Images tab (or double-click it)",
            clicked=self._on_view,
        )
        self._graph = widgets.button(
            "Size Graph",
            "chart",
            "Graph each layer's EXR size across the job's frames, odd ones in red",
            clicked=self._show_graph,
        )
        self._tasks.add_buttons(self._graph, self._view)

        jobs_title = QtWidgets.QLabel("Tractor jobs for this shot")
        jobs_title.setStyleSheet("QLabel { font-weight: 600; }")
        top = QtWidgets.QHBoxLayout()
        top.addWidget(widgets.icon_label("server"))
        top.addWidget(jobs_title)
        top.addStretch(1)
        top.addWidget(self._status)
        top.addWidget(self._forget)
        top.addWidget(self._archive_button)
        top.addWidget(refresh)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(self._jobs)
        split.addWidget(self._tasks)
        split.setSizes([260, 520])

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addLayout(top)
        layout.addWidget(split, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._enable()
        self._show_login()

    def show_job(self, shot_code: str, jid: int) -> None:
        """Show `shot_code`'s jobs with job `jid` chosen, as the Overview asks."""
        self._wanted_jid = jid
        if shot_code == self._shot:
            self.refresh()
        else:
            self.set_shot(shot_code)

    def set_shot(self, shot_code: str) -> None:
        if shot_code == self._shot:
            return
        self._shot = shot_code
        self._jobs.clear()
        self._tasks.show_job(None)
        self.refresh()

    def refresh(self) -> None:
        if self._shot is None:
            return
        shot = self._shot
        self._status.setText("Asking Tractor…")
        background.run(
            lambda: farm.shot_jobs(shot),
            lambda jobs: self._jobs_read(shot, jobs),
            self._failed,
        )

    # ---- jobs ----------------------------------------------------------

    def _tick(self) -> None:
        # Only while someone can see it.
        if self.isVisible():
            self.refresh()

    def _jobs_read(self, shot: str, jobs: list[Job]) -> None:
        if shot != self._shot:
            return
        shown = self._tasks.job
        chosen = self._wanted_jid or (shown.jid if shown else None)
        wanted, self._wanted_jid = self._wanted_jid, None
        # Kept across refreshes, so jobs being chosen to archive stay chosen.
        selected = {job.jid for job in self._selected_jobs()}
        self._jobs.blockSignals(True)
        self._jobs.clear()
        current = None
        for job in jobs:
            sent = job.spooled.strftime("%b %d %H:%M") if job.spooled else ""
            item = widgets.SortableItem(
                [
                    str(job.jid),
                    job.title,
                    job.owner,
                    sent,
                    f"{job.done}/{job.total}",
                    str(job.errors or ""),
                    job.status,
                ]
            )
            item.setData(0, _DATA_ROLE, job)
            item.setData(
                _SENT,
                widgets.SORT_ROLE,
                job.spooled.timestamp() if job.spooled else 0.0,
            )
            _color_row(item, *_JOB_ROWS.get(job.status, (_GREY, None)))
            self._jobs.addTopLevelItem(item)
            if job.jid == chosen:
                current = item
        if current is not None and wanted is not None:
            # Asked for by the Overview: choose it alone, and show it.
            self._jobs.setCurrentItem(current)
            self._jobs.scrollToItem(current)
            selected = {wanted}
        elif current is not None:
            # Only the row shown below; selecting it would add it to an archive.
            self._jobs.setCurrentItem(current, 0, QtCore.QItemSelectionModel.NoUpdate)
        for index in range(self._jobs.topLevelItemCount()):
            item = self._jobs.topLevelItem(index)
            item.setSelected(item.data(0, _DATA_ROLE).jid in selected)
        self._jobs.blockSignals(False)
        self._enable()
        for column in range(self._jobs.columnCount()):
            self._jobs.resizeColumnToContents(column)
        errored = sum(1 for j in jobs if j.errors)
        note, self._note = self._note, ""
        self._status.setText(
            f"{note + ' · ' if note else ''}{len(jobs)} jobs, {errored} with errors · "
            f"updated {time.strftime('%H:%M:%S')}"
        )
        if wanted is not None and current is not None:
            self._job_chosen()
        elif self._jobs.currentItem() is None and self._jobs.topLevelItemCount():
            self._jobs.setCurrentItem(self._jobs.topLevelItem(0))
        elif shown is not None:
            # Refresh the open job's tasks too, keeping the artist's place.
            self._job_chosen(keep_log=True)

    def _job_chosen(self, keep_log: bool = False) -> None:
        item = self._jobs.currentItem()
        job: Job | None = item.data(0, _DATA_ROLE) if item is not None else None
        if job is None:
            return
        self._tasks.show_job(job, keep_log)
        self._enable()

    def _said(self, text: str) -> None:
        self._status.setText(text)

    def _noted(self, text: str) -> None:
        self._note = text

    # ---- actions -------------------------------------------------------

    def _login_failed(self, exc: Exception) -> None:
        if isinstance(exc, LoginRefused):
            self._login.refused()
        self._failed(exc)
        QtCore.QTimer.singleShot(1000, self.refresh)

    def _archive(self) -> None:
        jobs = self._selected_jobs()
        if not jobs or not self._confirm_archive(jobs):
            return
        owners = sorted({job.owner for job in jobs})
        login = self._login.current(self, owners, "Archiving")
        if login is None:
            return
        user, password = login
        self._status.setText(f"Archiving {_count(len(jobs), 'job')}…")
        background.run(
            lambda: farm.archive(jobs, user, password),
            self._archived,
            self._login_failed,
        )

    def _confirm_archive(self, jobs: list[Job]) -> bool:
        listed = "\n".join(
            f"  {job.jid}  {job.title}  ({job.owner})" for job in jobs[:12]
        )
        if len(jobs) > 12:
            listed += f"\n  …and {len(jobs) - 12} more"
        unfinished = [job for job in jobs if job.done < job.total]
        warning = (
            f"\n\n{_count(len(unfinished), 'of them is', 'of them are')} not finished: "
            "archiving stops it, and Tractor kills any frames it is rendering."
            if unfinished
            else ""
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Archive Jobs",
            f"Archive {_count(len(jobs), 'job')} from Tractor?\n\n{listed}{warning}\n\n"
            "Archived jobs leave this list. Their frames stay on disk, and Tractor's "
            "dashboard can restore them from its archive.",
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _archived(self, archived: list[int]) -> None:
        # Shown with the refreshed list, which would otherwise replace it at once.
        self._note = (
            f"Archived {_count(len(archived), 'job')}: {', '.join(map(str, archived))}"
        )
        if self._tasks.job is not None and self._tasks.job.jid in archived:
            self._tasks.show_job(None)
        self.refresh()

    def _forget_login(self) -> None:
        self._login.forget()
        self._status.setText(
            "Forgot the Tractor login. Retry or Archive will ask for it."
        )

    def _show_login(self) -> None:
        user = self._login.user()
        self._forget.setVisible(bool(user))
        self._forget.setToolTip(f"Forget the Tractor login of {user}" if user else "")

    def _on_view(self) -> None:
        job, task = self._tasks.job, self._tasks.current_task()
        if job is None:
            return
        folders = self._tasks.folders or job.metadata.get("folders", {})
        match = _RENDER_TASK.match(task.title) if task is not None else None
        if match:
            layer, frame = match[1], int(match[2])
        else:
            layer, frame = next(iter(folders), ""), None
        if not layer:
            self._status.setText("This job's tasks don't name a render layer to show.")
            return
        folder = folders.get(layer)
        self.view_frames.emit(layer, Path(folder) if folder else None, frame)

    def _show_graph(self) -> None:
        job = self._tasks.job
        if job is None or not self._tasks.sizes:
            return
        dialog = SizeGraphDialog(
            f"Frame Sizes — {self._shot}, job {job.jid}", self._tasks.sizes, self
        )
        dialog.show()

    # ---- helpers -------------------------------------------------------

    def _selected_jobs(self) -> list[Job]:
        return [item.data(0, _DATA_ROLE) for item in self._jobs.selectedItems()]

    def _enable(self) -> None:
        has_job = self._tasks.job is not None
        self._view.setEnabled(has_job)
        self._graph.setEnabled(has_job and any(self._tasks.sizes.values()))
        self._archive_button.setEnabled(bool(self._jobs.selectedItems()))

    def _failed(self, exc: Exception) -> None:
        self._status.setText(
            str(exc) if isinstance(exc, FarmError) else f"Tractor: {exc}"
        )


# Qt 6 moved QShortcut from QtWidgets to QtGui.
_shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut


def _table(headers: list[str]) -> QtWidgets.QTreeWidget:
    table = QtWidgets.QTreeWidget()
    table.setHeaderLabels(headers)
    table.setRootIsDecorated(False)
    table.setUniformRowHeights(True)
    table.setAlternatingRowColors(True)
    widgets.sortable(table)
    return table


def _color_row(item: QtWidgets.QTreeWidgetItem, text: str, fill: str | None) -> None:
    for column in range(item.columnCount()):
        item.setForeground(column, QtGui.QBrush(QtGui.QColor(text)))
        if fill is not None:
            item.setBackground(column, QtGui.QBrush(QtGui.QColor(fill)))


def _count(n: int, one: str, many: str | None = None) -> str:
    """'1 job', '3 jobs'; or with `many`, '1 of them is', '2 of them are'."""
    return f"{n} {one if n == 1 else many or one + 's'}"
