"""A Tractor job's tasks and the log of the one chosen, with retrying what
errored: the lower half of the Farm tab and of the Tractor Overview.

Retrying needs a Tractor login, which both share for as long as the window
is open (`shared_login`), and which can be remembered in the desktop keyring.
"""

from __future__ import annotations

import getpass
import re
from pathlib import Path

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render import farm
from pipe.core.render.farm import ERROR, FarmError, Job, LoginRefused, Task
from pipe.core.render.network import background, credentials, sizes, theme, widgets
from pipe.core.render.network.progress import PERCENT_ROLE, ProgressDelegate
from pipe.core.render.network.sizes import FrameSize

_DATA_ROLE = QtCore.Qt.UserRole
# The task table's progress, size and time columns.
_PROGRESS, _SIZE, _TIME = 2, 3, 6
RED, GREEN, GREY, DIM = "#f08080", "#8fe0a0", "#a3a6ad", "#686b72"
# A task's state, in the jobs' colors.
_STATUS_COLORS = {
    ERROR: RED,
    "Active": GREEN,
    "Ready": GREY,
    "Blocked": GREY,
    "Done": DIM,
}
_ODD_SIZE = ("#ffd27a", "#4a3a1c")
_LOG_ERROR = re.compile(r"error|fatal|segmentation|killed|traceback", re.IGNORECASE)


class TractorLogin(QtCore.QObject):
    """The Tractor login for retrying and archiving, kept while the window is
    open. `changed` follows a login given, forgotten or refused; `note` says
    something the artist should know about it."""

    changed = QtCore.Signal()
    note = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._login: tuple[str, str] | None = None

    def current(
        self, parent: QtWidgets.QWidget, owners: list[str], doing: str
    ) -> tuple[str, str] | None:
        """The login to use: this session's, else the remembered one, else asked
        for; None if the artist cancels."""
        if self._login is None:
            # Read only when needed: the keyring may ask to be unlocked.
            self._login = credentials.load()
        return self._login or self._ask(parent, owners, doing)

    def user(self) -> str | None:
        return self._login[0] if self._login else credentials.remembered_user()

    def forget(self) -> None:
        self._login = None
        credentials.forget()
        self.changed.emit()

    def refused(self) -> None:
        """Ask again next time rather than repeat a wrong password, which also
        drops a remembered one, such as after a password change."""
        self.forget()

    def _ask(
        self, parent: QtWidgets.QWidget, owners: list[str], doing: str
    ) -> tuple[str, str] | None:
        dialog = _LoginDialog(parent, owners, doing)
        if not dialog.exec_():
            return None
        self._login = dialog.login()
        if dialog.remember() and not credentials.save(*self._login):
            self.note.emit(
                "No keyring on this computer, so the login lasts until the window closes"
            )
        self.changed.emit()
        return self._login


_SHARED: TractorLogin | None = None


def shared_login() -> TractorLogin:
    """The window's one Tractor login, shared by every tab that needs it."""
    global _SHARED
    if _SHARED is None:
        _SHARED = TractorLogin()
    return _SHARED


class JobTasks(QtWidgets.QWidget):
    """A job's tasks beside the log of the one chosen.

    `message` carries a line for the owner's status; `changed` follows a retry,
    or a failed one, after which the owner should read the farm again;
    `loaded` follows each reading of the tasks; `activated` a double-clicked
    task.
    """

    message = QtCore.Signal(str)
    changed = QtCore.Signal()
    loaded = QtCore.Signal()
    activated = QtCore.Signal()

    _tasks: QtWidgets.QTreeWidget
    _log: QtWidgets.QPlainTextEdit
    _errors_only: QtWidgets.QCheckBox
    _retry_selected: QtWidgets.QPushButton
    _retry_errors: QtWidgets.QPushButton
    _bar: QtWidgets.QHBoxLayout
    _job: Job | None
    _task_list: list[Task]
    # Each render layer's frame sizes, and the version folder it writes.
    _sizes: dict[str, list[FrameSize]]
    _folders: dict[str, str]

    def __init__(
        self, login: TractorLogin, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._login = login
        self._job, self._task_list, self._sizes, self._folders = None, [], {}, {}

        self._tasks = QtWidgets.QTreeWidget()
        self._tasks.setHeaderLabels(
            ["Task", "State", "Progress", "Size", "Blade", "Exit", "Time"]
        )
        self._tasks.setRootIsDecorated(False)
        self._tasks.setUniformRowHeights(True)
        self._tasks.setAlternatingRowColors(True)
        widgets.sortable(self._tasks)
        self._tasks.setItemDelegateForColumn(_PROGRESS, ProgressDelegate(self._tasks))
        self._tasks.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._tasks.currentItemChanged.connect(lambda *_: self._task_chosen())
        self._tasks.itemDoubleClicked.connect(lambda *_: self.activated.emit())
        self._tasks.itemSelectionChanged.connect(self._enable)
        self._log = QtWidgets.QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self._log.setPlaceholderText("Choose a task to read its log.")

        self._errors_only = QtWidgets.QCheckBox("Errors only")
        self._errors_only.toggled.connect(lambda _: self._show_tasks())
        self._retry_selected = widgets.button(
            "Retry Selected",
            "retry",
            "Run the selected tasks again",
            clicked=lambda: self.retry(selected=True),
        )
        self._retry_errors = widgets.button(
            "Retry All Errors",
            "retry",
            "Run every errored task of the job again (R)",
            theme.PRIMARY,
            lambda: self.retry(selected=False),
        )
        self._bar = QtWidgets.QHBoxLayout()
        self._bar.addWidget(self._errors_only)
        self._bar.addStretch(1)
        self._bar.addWidget(widgets.separator())
        self._bar.addWidget(self._retry_selected)
        self._bar.addWidget(self._retry_errors)

        tasks_box = QtWidgets.QWidget()
        tasks_layout = QtWidgets.QVBoxLayout(tasks_box)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.addLayout(self._bar)
        tasks_layout.addWidget(self._tasks)
        split = QtWidgets.QSplitter()
        split.addWidget(tasks_box)
        split.addWidget(self._log)
        split.setSizes([520, 620])
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self._enable()

    @property
    def job(self) -> Job | None:
        return self._job

    @property
    def sizes(self) -> dict[str, list[FrameSize]]:
        return self._sizes

    @property
    def folders(self) -> dict[str, str]:
        return self._folders

    def add_buttons(self, *buttons: QtWidgets.QWidget) -> None:
        """Put `buttons` in the task bar, before the retry buttons."""
        at = self._bar.indexOf(self._retry_selected) - 1
        for button in buttons:
            self._bar.insertWidget(at, button)
            at += 1

    def show_job(self, job: Job | None, keep_log: bool = False) -> None:
        """Show `job`'s tasks; with `keep_log`, keep the log shown if it is the
        same job, as when the farm is read again."""
        if job is None:
            self._job, self._task_list, self._sizes, self._folders = None, [], {}, {}
            self._tasks.clear()
            self._log.clear()
            self._enable()
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

    def current_task(self) -> Task | None:
        item = self._tasks.currentItem()
        return item.data(0, _DATA_ROLE) if item is not None else None

    def retry(self, selected: bool) -> None:
        """Retry the selected tasks, or with `selected` False, every errored one."""
        job = self._job
        if job is None:
            return
        tids = [t.tid for t in self._selected_tasks()] if selected else []
        if selected and not tids:
            return
        login = self._login.current(self, [job.owner], "Retrying")
        if login is None:
            return
        what = (
            f"{len(tids)} task{'s' if len(tids) != 1 else ''}"
            if tids
            else "every errored task"
        )
        self.message.emit(f"Retrying {what} of job {job.jid}…")
        user, password = login
        background.run(
            lambda: farm.retry(job, tids, user, password),
            lambda _: self._retried(job, what),
            self._retry_failed,
        )

    def retry_errors_key(self) -> None:
        """R: retry the job's errored tasks, as Retry All Errors does."""
        job = self._job
        if job is None:
            self.message.emit("Choose a job to retry its errors.")
        elif not job.errors:
            self.message.emit(f"Job {job.jid} has no errored tasks to retry.")
        else:
            self.retry(selected=False)

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
        self.loaded.emit()

    def _show_tasks(self) -> None:
        chosen = {t.tid for t in self._selected_tasks()}
        current = self.current_task()
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
            item = widgets.SortableItem(
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
            # Numbers to sort by, as the cells show a bar, "1.2 GB" and "1:02:05".
            item.setData(_PROGRESS, widgets.SORT_ROLE, task.percent or -1)
            item.setData(_SIZE, widgets.SORT_ROLE, -1)
            item.setData(
                _TIME, widgets.SORT_ROLE, seconds if seconds is not None else -1.0
            )
            if (match := farm.RENDER_TASK.match(task.title)) and (
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
        job, task = self._job, self.current_task()
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
        if (current := self.current_task()) is None or current.tid != task.tid:
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

    # ---- retrying ------------------------------------------------------

    def _retried(self, job: Job, what: str) -> None:
        self.message.emit(f"Tractor is running {what} of job {job.jid} again.")
        self.changed.emit()

    def _retry_failed(self, exc: Exception) -> None:
        if isinstance(exc, LoginRefused):
            self._login.refused()
        self._failed(exc)
        self.changed.emit()

    # ---- helpers -------------------------------------------------------

    def _selected_tasks(self) -> list[Task]:
        return [item.data(0, _DATA_ROLE) for item in self._tasks.selectedItems()]

    def _enable(self) -> None:
        job = self._job
        self._retry_errors.setEnabled(job is not None and bool(job.errors))
        self._retry_selected.setEnabled(
            job is not None and bool(self._tasks.selectedItems())
        )

    def _failed(self, exc: Exception) -> None:
        self.message.emit(str(exc) if isinstance(exc, FarmError) else f"Tractor: {exc}")


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


def _show_size(item: QtWidgets.QTreeWidgetItem, size: FrameSize) -> None:
    item.setText(_SIZE, sizes.human(size.bytes))
    item.setData(_SIZE, widgets.SORT_ROLE, size.bytes)
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
