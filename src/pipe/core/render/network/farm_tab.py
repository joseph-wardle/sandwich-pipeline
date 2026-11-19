"""The Farm tab: every Tractor job for the shot, its tasks and their logs, and
retrying what errored.

Each render task shows the size of the EXR it wrote, flagged when it is far
from the frames around it, and Size Graph draws them across the shot.

The farm already reruns a render frame by itself when it crashes, runs out of
memory or misses a license (`job.RENDER_RETRIES`); what errors past that is
listed here to read and retry by hand.
"""

from __future__ import annotations

import getpass
import re
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
    Task,
)
from pipe.core.render.network import background, credentials, sizes, theme, widgets
from pipe.core.render.network.progress import PERCENT_ROLE, ProgressDelegate
from pipe.core.render.network.size_graph import SizeGraphDialog
from pipe.core.render.network.sizes import FrameSize

_DATA_ROLE = QtCore.Qt.UserRole
_REFRESH_MS = 30_000
# The task table's progress and size columns.
_PROGRESS, _SIZE = 2, 3
_RENDER_TASK = farm.RENDER_TASK
_RED, _GREEN, _GREY, _DIM = "#f08080", "#8fe0a0", "#a3a6ad", "#686b72"
# A job's whole row: (text, background or None). Errored and active jobs
# stand out; done and waiting ones step back.
_JOB_ROWS: dict[str, tuple[str, str | None]] = {
    ERROR: (_RED, "#40252a"),
    RENDERING: (_GREEN, "#1f3a28"),
    PAUSED: ("#c9a14a", None),
    WAITING: (_GREY, None),
    "Done": (_DIM, None),
}
# A task's state, in the jobs' colors.
_STATUS_COLORS = {
    ERROR: _RED,
    "Active": _GREEN,
    "Ready": _GREY,
    "Blocked": _GREY,
    "Done": _DIM,
}
_ODD_SIZE = ("#ffd27a", "#4a3a1c")
_LOG_ERROR = re.compile(r"error|fatal|segmentation|killed|traceback", re.IGNORECASE)


class FarmTab(QtWidgets.QWidget):
    """`view_frames` asks the Images tab for a layer, its folder or None, and a frame or None."""

    view_frames = QtCore.Signal(str, object, object)

    _jobs: QtWidgets.QTreeWidget
    _tasks: QtWidgets.QTreeWidget
    _log: QtWidgets.QPlainTextEdit
    _status: QtWidgets.QLabel
    _errors_only: QtWidgets.QCheckBox
    _retry_selected: QtWidgets.QPushButton
    _retry_errors: QtWidgets.QPushButton
    _archive_button: QtWidgets.QPushButton
    _forget: QtWidgets.QPushButton
    _view: QtWidgets.QPushButton
    _graph: QtWidgets.QPushButton
    _timer: QtCore.QTimer

    _shot: str | None
    _job: Job | None
    _task_list: list[Task]
    # Each render layer's frame sizes, for the job shown.
    _sizes: dict[str, list[FrameSize]]
    # The version folder each layer of the job shown writes, by layer.
    _folders: dict[str, str]
    # The Tractor login for retries, kept only while the window is open.
    _login: tuple[str, str] | None
    # What the last action did, shown once with the next refreshed list.
    _note: str

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._shot, self._job, self._task_list, self._login = None, None, [], None
        self._sizes, self._folders = {}, {}
        self._wanted_jid: int | None = None
        self._note = ""

        self._jobs = _table(
            ["Job", "Title", "Owner", "Sent", "Frames", "Errors", "Status"]
        )
        self._jobs.currentItemChanged.connect(lambda *_: self._job_chosen())
        self._jobs.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._jobs.itemSelectionChanged.connect(self._enable)
        self._tasks = _table(
            ["Task", "State", "Progress", "Size", "Blade", "Exit", "Time"]
        )
        self._tasks.setItemDelegateForColumn(_PROGRESS, ProgressDelegate(self._tasks))
        self._tasks.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._tasks.currentItemChanged.connect(lambda *_: self._task_chosen())
        self._tasks.itemDoubleClicked.connect(lambda *_: self._on_view())
        self._tasks.itemSelectionChanged.connect(self._enable)
        self._log = QtWidgets.QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self._log.setPlaceholderText("Choose a task to read its log.")

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
        self._errors_only = QtWidgets.QCheckBox("Errors only")
        self._errors_only.toggled.connect(lambda _: self._show_tasks())
        self._retry_selected = widgets.button(
            "Retry Selected",
            "retry",
            "Run the selected tasks again",
            clicked=lambda: self._retry(selected=True),
        )
        self._retry_errors = widgets.button(
            "Retry All Errors",
            "retry",
            "Run every errored task of the selected job again (R)",
            theme.PRIMARY,
            lambda: self._retry(selected=False),
        )
        # Anywhere in the Farm tab, and nowhere else in the window.
        retry_key = _shortcut(QtGui.QKeySequence("R"), self)
        retry_key.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        retry_key.activated.connect(self._retry_errors_key)
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
        task_bar = QtWidgets.QHBoxLayout()
        task_bar.addWidget(self._errors_only)
        task_bar.addStretch(1)
        task_bar.addWidget(self._graph)
        task_bar.addWidget(self._view)
        task_bar.addWidget(widgets.separator())
        task_bar.addWidget(self._retry_selected)
        task_bar.addWidget(self._retry_errors)

        tasks_box = QtWidgets.QWidget()
        tasks_layout = QtWidgets.QVBoxLayout(tasks_box)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.addLayout(task_bar)
        tasks_layout.addWidget(self._tasks)
        lower = QtWidgets.QSplitter()
        lower.addWidget(tasks_box)
        lower.addWidget(self._log)
        lower.setSizes([520, 620])
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(self._jobs)
        split.addWidget(lower)
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
        self._shot, self._job, self._task_list, self._sizes = shot_code, None, [], {}
        self._jobs.clear()
        self._tasks.clear()
        self._log.clear()
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
        chosen = self._wanted_jid or (self._job.jid if self._job else None)
        wanted, self._wanted_jid = self._wanted_jid, None
        # Kept across refreshes, so jobs being chosen to archive stay chosen.
        selected = {job.jid for job in self._selected_jobs()}
        self._jobs.blockSignals(True)
        self._jobs.clear()
        current = None
        for job in jobs:
            sent = job.spooled.strftime("%b %d %H:%M") if job.spooled else ""
            item = QtWidgets.QTreeWidgetItem(
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
        elif self._job is not None:
            # Refresh the open job's tasks too, keeping the artist's place.
            self._job_chosen(keep_log=True)

    def _job_chosen(self, keep_log: bool = False) -> None:
        item = self._jobs.currentItem()
        job: Job | None = item.data(0, _DATA_ROLE) if item is not None else None
        if job is None:
            return
        same = self._job is not None and self._job.jid == job.jid
        self._job = job
        if not (same and keep_log):
            self._log.clear()
        background.run(
            lambda: _tasks_and_sizes(job),
            lambda found: self._tasks_read(job, *found),
            self._failed,
        )
        self._enable()

    # ---- tasks ---------------------------------------------------------

    def _tasks_read(
        self,
        job: Job,
        found: list[Task],
        folders: dict[str, str],
        layer_sizes: dict[str, list[FrameSize]],
    ) -> None:
        if self._job is None or job.jid != self._job.jid:
            return
        self._task_list, self._folders, self._sizes = found, folders, layer_sizes
        self._show_tasks()

    def _show_tasks(self) -> None:
        chosen = {t.tid for t in self._selected_tasks()}
        current = self._current_task()
        self._tasks.blockSignals(True)
        self._tasks.clear()
        by_frame = {
            layer: {size.frame: size for size in found}
            for layer, found in self._sizes.items()
        }
        for task in self._task_list:
            if self._errors_only.isChecked() and task.state != ERROR:
                continue
            seconds = task.seconds
            item = QtWidgets.QTreeWidgetItem(
                [
                    task.title,
                    task.state,
                    "",
                    "",
                    task.blade,
                    "" if task.exit_code is None else str(task.exit_code),
                    _duration(seconds)
                    if seconds is not None and task.state != "Ready"
                    else "",
                ]
            )
            item.setData(0, _DATA_ROLE, task)
            if color := _STATUS_COLORS.get(task.state):
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(color)))
            item.setData(_PROGRESS, PERCENT_ROLE, task.percent)
            if (match := _RENDER_TASK.match(task.title)) and (
                size := by_frame.get(match[1], {}).get(int(match[2]))
            ):
                _show_size(item, size)
            self._tasks.addTopLevelItem(item)
            item.setSelected(task.tid in chosen)
            if current is not None and task.tid == current.tid:
                self._tasks.setCurrentItem(item)
        self._tasks.blockSignals(False)
        self._tasks.resizeColumnToContents(0)
        self._tasks.setColumnWidth(_PROGRESS, 110)
        self._tasks.resizeColumnToContents(_SIZE)
        self._enable()

    def _task_chosen(self) -> None:
        job, task = self._job, self._current_task()
        if job is None or task is None:
            return
        self._log.setPlainText(f"Reading the log of {task.title}…")
        background.run(
            lambda: farm.log(job, task),
            lambda text: self._log_read(task, text),
            lambda exc: self._log.setPlainText(str(exc)),
        )
        self._enable()

    def _log_read(self, task: Task, text: str) -> None:
        if (current := self._current_task()) is None or current.tid != task.tid:
            return
        tries = farm.attempts(text)
        header = (
            f"{task.title}: {task.state}, {tries} attempt{'s' if tries != 1 else ''}"
        )
        if task.exit_code not in (None, 0):
            header += f", last exit code {task.exit_code}"
        self._log.setPlainText(f"{header}\n\n{text}")
        self._highlight_errors()
        self._log.moveCursor(QtGui.QTextCursor.End)

    def _highlight_errors(self) -> None:
        selections = []
        document = self._log.document()
        block = document.begin()
        while block.isValid():
            if _LOG_ERROR.search(block.text()):
                selection = QtWidgets.QTextEdit.ExtraSelection()
                selection.format.setBackground(QtGui.QColor(110, 40, 40))
                selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
                selection.cursor = QtGui.QTextCursor(block)
                selections.append(selection)
            block = block.next()
        self._log.setExtraSelections(selections)

    # ---- actions -------------------------------------------------------

    def _retry(self, selected: bool) -> None:
        job = self._job
        if job is None:
            return
        tids = [t.tid for t in self._selected_tasks()] if selected else []
        if selected and not tids:
            return
        login = self._current_login([job.owner], "Retrying")
        if login is None:
            return
        what = (
            f"{len(tids)} task{'s' if len(tids) != 1 else ''}"
            if tids
            else "every errored task"
        )
        self._status.setText(f"Retrying {what} of job {job.jid}…")
        user, password = login
        background.run(
            lambda: farm.retry(job, tids, user, password),
            lambda _: self._retried(job, what),
            self._login_failed,
        )

    def _retry_errors_key(self) -> None:
        """R: retry the selected job's errored tasks, as Retry All Errors does."""
        job = self._job
        if job is None:
            self._status.setText("Choose a job to retry its errors.")
        elif not job.errors:
            self._status.setText(f"Job {job.jid} has no errored tasks to retry.")
        else:
            self._retry(selected=False)

    def _retried(self, job: Job, what: str) -> None:
        self._status.setText(f"Tractor is running {what} of job {job.jid} again.")
        QtCore.QTimer.singleShot(2000, self.refresh)

    def _login_failed(self, exc: Exception) -> None:
        if isinstance(exc, LoginRefused):
            # Ask again next time rather than repeat a wrong password, which
            # also drops a remembered one, such as after a password change.
            self._login = None
            credentials.forget()
            self._show_login()
        self._failed(exc)
        QtCore.QTimer.singleShot(1000, self.refresh)

    def _archive(self) -> None:
        jobs = self._selected_jobs()
        if not jobs or not self._confirm_archive(jobs):
            return
        owners = sorted({job.owner for job in jobs})
        login = self._current_login(owners, "Archiving")
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
        if self._job is not None and self._job.jid in archived:
            self._job, self._task_list, self._sizes = None, [], {}
            self._tasks.clear()
            self._log.clear()
        self.refresh()

    def _current_login(self, owners: list[str], doing: str) -> tuple[str, str] | None:
        """The login to use: this session's, else the remembered one, else asked for."""
        if self._login is None:
            # Read only when needed: the keyring may ask to be unlocked.
            self._login = credentials.load()
        return self._login or self._ask_login(owners, doing)

    def _ask_login(self, owners: list[str], doing: str) -> tuple[str, str] | None:
        dialog = _LoginDialog(self, owners, doing)
        if not dialog.exec_():
            return None
        self._login = dialog.login()
        if dialog.remember() and not credentials.save(*self._login):
            self._note = "No keyring on this computer, so the login lasts until the window closes"
        self._show_login()
        return self._login

    def _forget_login(self) -> None:
        self._login = None
        credentials.forget()
        self._show_login()
        self._status.setText(
            "Forgot the Tractor login. Retry or Archive will ask for it."
        )

    def _show_login(self) -> None:
        user = self._login[0] if self._login else credentials.remembered_user()
        self._forget.setVisible(bool(user))
        self._forget.setToolTip(f"Forget the Tractor login of {user}" if user else "")

    def _on_view(self) -> None:
        job, task = self._job, self._current_task()
        if job is None:
            return
        folders = self._folders or job.metadata.get("folders", {})
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
        job = self._job
        if job is None or not self._sizes:
            return
        dialog = SizeGraphDialog(
            f"Frame Sizes — {self._shot}, job {job.jid}", self._sizes, self
        )
        dialog.show()

    # ---- helpers -------------------------------------------------------

    def _current_task(self) -> Task | None:
        item = self._tasks.currentItem()
        return item.data(0, _DATA_ROLE) if item is not None else None

    def _selected_jobs(self) -> list[Job]:
        return [item.data(0, _DATA_ROLE) for item in self._jobs.selectedItems()]

    def _selected_tasks(self) -> list[Task]:
        return [item.data(0, _DATA_ROLE) for item in self._tasks.selectedItems()]

    def _enable(self) -> None:
        has_job = self._job is not None
        self._retry_errors.setEnabled(has_job and bool(self._job and self._job.errors))
        self._retry_selected.setEnabled(has_job and bool(self._tasks.selectedItems()))
        self._view.setEnabled(has_job)
        self._graph.setEnabled(has_job and any(self._sizes.values()))
        self._archive_button.setEnabled(bool(self._jobs.selectedItems()))

    def _failed(self, exc: Exception) -> None:
        self._status.setText(
            str(exc) if isinstance(exc, FarmError) else f"Tractor: {exc}"
        )


class _LoginDialog(QtWidgets.QDialog):
    """Asks for the Tractor login that retrying and archiving need."""

    def __init__(
        self, parent: QtWidgets.QWidget, owners: list[str], doing: str
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tractor Login")
        self._user = QtWidgets.QLineEdit(
            credentials.remembered_user() or getpass.getuser()
        )
        self._remember = QtWidgets.QCheckBox("Remember for this user")
        self._remember.setChecked(True)
        self._remember.setToolTip(
            "Keep the password in your desktop keyring, encrypted and unlocked by "
            "your login. Forget Login on the Farm tab removes it."
        )
        self._password = QtWidgets.QLineEdit()
        self._password.setEchoMode(QtWidgets.QLineEdit.Password)
        note = QtWidgets.QLabel(
            f"{doing} needs the Tractor login of the jobs' owner ({', '.join(owners)}) "
            "or of a Tractor administrator."
        )
        note.setWordWrap(True)
        form = QtWidgets.QFormLayout()
        form.addRow("User", self._user)
        form.addRow("Password", self._password)
        form.addRow("", self._remember)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._password.setFocus()

    def login(self) -> tuple[str, str]:
        return self._user.text().strip(), self._password.text()

    def remember(self) -> bool:
        return self._remember.isChecked()


# Qt 6 moved QShortcut from QtWidgets to QtGui.
_shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut


def _table(headers: list[str]) -> QtWidgets.QTreeWidget:
    table = QtWidgets.QTreeWidget()
    table.setHeaderLabels(headers)
    table.setRootIsDecorated(False)
    table.setUniformRowHeights(True)
    table.setAlternatingRowColors(True)
    return table


def _tasks_and_sizes(
    job: Job,
) -> tuple[list[Task], dict[str, str], dict[str, list[FrameSize]]]:
    """The job's tasks, its layers' folders, and each layer's frame sizes."""
    found = farm.tasks(job)
    folders = farm.folders(job, found)
    layer_sizes = {}
    for layer, folder in folders.items():
        path = Path(folder)
        layer_sizes[layer] = sizes.layer_sizes(path) if path.is_dir() else []
    return found, folders, layer_sizes


def _color_row(item: QtWidgets.QTreeWidgetItem, text: str, fill: str | None) -> None:
    for column in range(item.columnCount()):
        item.setForeground(column, QtGui.QBrush(QtGui.QColor(text)))
        if fill is not None:
            item.setBackground(column, QtGui.QBrush(QtGui.QColor(fill)))


def _show_size(item: QtWidgets.QTreeWidgetItem, size: FrameSize) -> None:
    item.setText(_SIZE, sizes.human(size.bytes))
    item.setTextAlignment(_SIZE, int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
    tip = f"{size.path}\n{size.bytes:,} bytes"
    if size.anomaly:
        text, fill = _ODD_SIZE
        item.setText(_SIZE, f"⚠ {sizes.human(size.bytes)}")
        item.setForeground(_SIZE, QtGui.QBrush(QtGui.QColor(text)))
        item.setBackground(_SIZE, QtGui.QBrush(QtGui.QColor(fill)))
        tip += f"\n{sizes.describe(size)}"
    item.setToolTip(_SIZE, tip)


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def _count(n: int, one: str, many: str | None = None) -> str:
    """'1 job', '3 jobs'; or with `many`, '1 of them is', '2 of them are'."""
    return f"{n} {one if n == 1 else many or one + 's'}"
