"""The Farm Doctor tab: why the production's Tractor tasks fail, which blades
fail them, and what the doctor has retried.

It runs the doctor's checks (`pipe.core.render.doctor`) itself, so it shows
the farm whether or not the headless doctor is running, and reads that
doctor's status and journal from its files. Retry Selected retries the chosen
tasks here and now, with the doctor's login.
"""

from __future__ import annotations

import getpass
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core import tractor
from pipe.core.render.doctor import login, records
from pipe.core.render.doctor import attempts, diagnose, windows
from pipe.core.render.doctor.blades import (
    LOW_DISK_GB,
    SILENT_SECONDS,
    WINDOW_SECONDS,
    BladeHealth,
    Failure,
    History,
)
from pipe.core.render.doctor.checkup import (
    PIN_SECONDS,
    Checkup,
    Doctor,
    Problem,
    Settings,
    group,
)
from pipe.core.render.doctor.diagnose import MAX_ATTEMPTS, Cause, Diagnosis, Remedy
from pipe.core.render.doctor.login import Connection, NoLogin
from pipe.core.render.doctor.policy import Action, Plan
from pipe.core.render.app import background, icons, job_status, labs, theme, widgets
from pipe.core.render.app.job_menu import JobMenu
from pipe.core.render.app.login import load as remembered_login
from pipe.core.render.app.login import shared_login
from pipe.core.render.app.lab_map import LabMapWindow
from pipe.core.render.app.attempts_panel import TriesPanel, TryRow

_REFRESH_MS = 60_000
_DATA_ROLE = QtCore.Qt.UserRole
# What a refresh reads: the farm, the doctor's status and journal, the machines
# in Windows, and those taken off the farm from the Machines tab.
_Found = tuple[
    "Checkup",
    dict[str, Any] | None,
    list[dict[str, Any]],
    set[str],
    dict[str, dict[str, Any]],
]
# A Problems row's (job, how it failed), and a job's several ways of failing.
_KEY_ROLE = QtCore.Qt.UserRole + 1
_KINDS_ROLE = QtCore.Qt.UserRole + 2
# Problems table columns that rows fill in after building them.
_STATUS, _PROBLEM, _NEXT = 2, 5, 6
_RED, _AMBER, _GREEN, _GREY = "#f08080", "#e0b060", "#8fe0a0", "#a3a6ad"
_ACTION_COLORS = {Action.HOLD: _RED, Action.WAIT: _GREY, Action.RETRY: _GREEN}
# Automatic retries that haven't checked in this long have likely stopped.
_STALE_SECONDS = 15 * 60
_RUN_COMMAND = "python -m pipe.core.render.doctor schedule --act"
# The doctor's words are a TD's. Artists see these instead, by cause key:
# (what went wrong, what it means and what to do).
_CAUSE_WORDS = {
    "disk_quota": (
        "Disk Quota Exceeded",
        "The folder it saves to has used up its storage limit (quota). Free up "
        "space there, then retry.",
    ),
    "shared_disk_full": (
        "Shared drive is full",
        "The shared drive it saves to is full. Free up space there, then retry.",
    ),
    "blade_disk_full": (
        "Machine's disk is full",
        "The farm machine's own disk is full. It should work on another machine.",
    ),
    "license": (
        "No license available",
        "Every license was in use for a moment. It works once one frees up.",
    ),
    "cook_error": (
        "Error in the Houdini scene",
        "A node in the hip file has an error, shown below. Fix it, then retry or "
        "send again.",
    ),
    "out_of_memory": (
        "Ran out of memory",
        "It needed more memory than the machine had free. It should work on a "
        "machine with more free memory.",
    ),
    "lost": (
        "Machine crashed (Lost the command)",
        "The machine went down partway through, often from running out of memory. "
        "It should work on another machine.",
    ),
    "crash": (
        "Crashed",
        "The renderer or Houdini crashed. This usually works on the next try.",
    ),
    "gpu": (
        "Graphics card error",
        "The machine's graphics card failed or ran out of memory. It should work on "
        "another machine.",
    ),
    "storage": (
        "Couldn't reach the shared drive",
        "The machine lost its connection to the shared drive. It should work on "
        "another machine.",
    ),
    "command_not_found": (
        "Software missing on the machine",
        "The machine is missing a program the task runs. If every machine is "
        "missing it, the job was set up wrong: tell a TD.",
    ),
    "missing_file": (
        "A file is missing",
        "It couldn't find a file it needs. If that happens on several machines, the "
        "file really is missing: publish it or fix the path, then retry.",
    ),
    "python_error": (
        "Pipeline script error",
        "A pipeline script hit an error. If it happens on several machines, the "
        "scene or the script needs fixing: tell a TD if it isn't yours.",
    ),
    "stopped": (
        "Stopped",
        "It was stopped partway, as when someone retries or kills it, or someone "
        "sits down at the machine. It works on the next try.",
    ),
    "unknown": (
        "Unrecognized error",
        "The Farm Doctor doesn't recognize this error. Read the log below.",
    ),
}
# The doctor's journal keeps a cause's label, not its key.
_KEY_OF_LABEL = {
    cause.label: cause.key
    for cause in vars(diagnose).values()
    if isinstance(cause, Cause)
} | {"Its blade stopped reporting": "lost"}
_ACTION_WORDS = {
    Action.RETRY: "Retry now",
    Action.WAIT: "Retry",
    Action.HOLD: "Needs a fix",
}
# The Machines table's columns that are colored or carry tooltips.
_OS_COLUMN, _RENDERING_COLUMN, _FAILED_COLUMN = 3, 6, 8
_LINUX, _WINDOWS = "Linux", "Windows"
_SYSTEM_COLORS = {_LINUX: "#8fe0a0", _WINDOWS: "#6ea8ff"}
_SYSTEM_TIPS = {
    _LINUX: "On Linux, so it can take farm work unless someone is using it",
    _WINDOWS: "Running Windows, so it takes no farm work until it's back on Linux",
}
# The cause the doctor gives a task its machine lost (exit code 223).
_LOST = "lost"
# husk's progress stamp, "[16:02:43/0:00]": the time, then how long it has rendered.
_HUSK_TIMER = re.compile(r"\[\d\d:\d\d:\d\d/(\d+):(\d\d)\]")
# A failed attempt's key, such as "J18230/T71/C68.3": its job and task.
_ATTEMPT_KEY = re.compile(r"^J(\d+)/T(\d+)/")
# Characters a log's file name shouldn't hold.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
# Where the window keeps its settings, and the folder logs were last saved to.
_SETTINGS = ("SandwichKwonDo", "RenderNetwork")
_LOG_FOLDER_KEY = "doctor/log_folder"
_SPAN_KEY = "doctor/tries_span"
# How far back a machine's failed tries are listed, as the artist chooses.
_SPANS = (
    ("Last hour", 3600),
    ("Last 24 hours", 24 * 3600),
    ("Last week", 7 * 24 * 3600),
    ("Last month", 31 * 24 * 3600),
)
_DEFAULT_SPAN = 1
_AVOIDED = re.compile(r"!'([^']+)'")
# A blade a task was sent to alone: 'yoshi', not after a "!".
_PINNED = re.compile(r"(?:^|&& |\s)'([^']+)'")
_MEMORY = re.compile(r"@\.mem > (\d+)")


class DoctorPage(QtWidgets.QWidget):
    """`open_job` carries a shot's code and a job id, to show on its Farm tab."""

    open_job = QtCore.Signal(str, int)
    # The Refresh button was clicked: the window refreshes its other Tractor
    # pages too, so all agree.
    refresh_all = QtCore.Signal()

    _problems: QtWidgets.QTreeWidget
    _detail: QtWidgets.QTextBrowser
    _machine_detail: QtWidgets.QTextBrowser
    # Each side pane's failed tries, which open their logs.
    _problem_tries: TriesPanel
    _machine_tries: TriesPanel
    _blades: QtWidgets.QTreeWidget
    _activity: QtWidgets.QTreeWidget
    _summary: QtWidgets.QLabel
    _doctor_status: widgets.Banner
    _status: QtWidgets.QLabel
    _mine: QtWidgets.QCheckBox
    _troubled: QtWidgets.QCheckBox
    _in_windows: QtWidgets.QCheckBox
    _retry: QtWidgets.QPushButton
    _open: QtWidgets.QPushButton
    _download: QtWidgets.QPushButton
    _machine_download: QtWidgets.QPushButton
    _timer: QtCore.QTimer
    _checkup: Checkup | None
    # The machines running Windows when the farm was last read.
    _windows: set[str]
    _busy: bool
    # The artist set "Steer retries" themselves, so it no longer follows the doctor.
    # The running doctor's last status, if any.
    _doctor_state: dict[str, Any] | None
    _scope_chosen: bool
    _production: QtWidgets.QCheckBox
    _job_menu: JobMenu
    # What a right-click last did, shown once with the next refreshed list.
    _note: str

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """Who may do what is Tractor's to say, with each artist's own login:
        nimbying, or retrying another's job, is refused to those without the
        right. Fresh starts and hyperfocus, which Tractor can't check, anyone
        may ask the automatic retries for, after a warning, through the
        production's shared folder (`records.Paths.shared`)."""
        super().__init__(parent)
        self._nimbied: dict[str, dict[str, Any]] = {}
        self._user = getpass.getuser()
        self._paths = records.default_paths()
        # Reads the headless doctor's history, so blades' records outlive their tasks.
        # Every job, so the tab can show what a doctor looking after them all
        # sees; "Only production jobs" narrows it.
        self._doctor = Doctor(
            History(self._paths.history), paths=self._paths, all_jobs=True
        )
        self._checkup, self._busy = None, False
        self._note = ""
        self._scope_chosen = False
        self._doctor_state = None
        self._windows = set()
        # What the logs say about each machine's lost tasks, by machine, with
        # the failures it was read for: read once, as it takes a log per task.
        self._lost_reports: dict[str, tuple[frozenset[str], _LostReport]] = {}

        title = widgets.PageHeading(
            "stethoscope",
            "Farm Doctor",
            "Why tasks failed, and which ones a retry will fix",
        )
        self._summary = QtWidgets.QLabel()
        self._summary.setStyleSheet("font-size: 13pt;")
        self._doctor_status = widgets.Banner()
        self._status = QtWidgets.QLabel()
        self._status.setEnabled(False)
        refresh = widgets.button(
            "Refresh",
            "refresh",
            "Read Tractor's logs again now, and every Tractor page with them",
            clicked=self._refresh_clicked,
        )

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._summary)
        status_bar = QtWidgets.QHBoxLayout()
        status_bar.addWidget(self._doctor_status, 1)
        status_bar.addWidget(self._status)
        status_bar.addWidget(refresh)

        tabs = QtWidgets.QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setIconSize(widgets.ICON_SIZE)
        tabs.addTab(self._build_problems(), icons.get("circle-alert"), "Problems")
        tabs.addTab(self._build_blades(), icons.get("cpu"), "Machines")
        tabs.addTab(self._build_activity(), icons.get("history"), "Activity")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addLayout(status_bar)
        layout.addWidget(tabs, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._enable()

    def _refresh_clicked(self) -> None:
        self.refresh()
        self.refresh_all.emit()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._status.setText("Reading Tractor's logs…")
        background.run(self._read_all, self._read, self._failed)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt's name)
        super().showEvent(event)
        if self._checkup is None:
            self.refresh()

    # ---- building ------------------------------------------------------

    def _build_problems(self) -> QtWidgets.QWidget:
        self._problems = _table(
            [
                "Job",
                "Shot",
                "Status",
                "Artist",
                "Tasks",
                "Problem",
                "Next step",
                "Tries",
                "Machines",
            ]
        )
        self._problems.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # A job's several ways of failing open beneath it.
        self._problems.setRootIsDecorated(True)
        self._problems.itemSelectionChanged.connect(self._problem_chosen)
        self._problems.itemDoubleClicked.connect(lambda *_: self._open_job())
        # Pause, Change Priority and Archive the chosen problems' jobs, on a
        # right-click, as on the Overview.
        self._job_menu = JobMenu(self, shared_login())
        self._job_menu.attach(
            self._problems,
            lambda items: [item.data(0, _DATA_ROLE).job for item in items],
        )
        self._job_menu.message.connect(self._status.setText)
        self._job_menu.changed.connect(self._job_acted)
        self._detail = QtWidgets.QTextBrowser()
        self._detail.setPlaceholderText("Choose a problem to see why its tasks failed.")
        self._mine = QtWidgets.QCheckBox("Only mine")
        self._mine.toggled.connect(lambda _: self._fill_problems())
        self._production = QtWidgets.QCheckBox("Only production jobs")
        self._production.setToolTip(
            "Only the Sandwich production's jobs, not class or test jobs. Starts "
            "the same as the automatic retries."
        )
        self._production.setChecked(True)
        self._production.toggled.connect(lambda _: self._fill_problems())
        self._production.clicked.connect(self._choose_scope)
        self._retry = widgets.button(
            "Retry Selected",
            "retry",
            "Retry the selected tasks now, as the automatic retries would: kept off "
            "the machines that failed them and those flagged in red, and on one with "
            "more free memory if they ran out",
            theme.PRIMARY,
            self._retry_selected,
        )
        self._open = widgets.button(
            "Open on Farm Tab",
            "external",
            "Show the job on its shot's Farm tab (or double-click it)",
            clicked=self._open_job,
        )

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(self._mine)
        bar.addWidget(self._production)
        bar.addStretch(1)
        self._download = widgets.button(
            "Download Logs",
            "download",
            "Save the selected tasks' Tractor logs to a folder you choose",
            clicked=self._download_problem_logs,
        )
        bar.addWidget(self._download)
        bar.addWidget(self._open)
        bar.addWidget(self._retry)
        self._problem_tries = TriesPanel(["Ended", "Task", "Try", "Machine", "Problem"])
        side = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        side.addWidget(self._detail)
        side.addWidget(self._problem_tries)
        side.setSizes([300, 460])
        split = QtWidgets.QSplitter()
        split.addWidget(self._problems)
        split.addWidget(side)
        split.setSizes([640, 640])
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.addLayout(bar)
        layout.addWidget(split, 1)
        return page

    def _build_blades(self) -> QtWidgets.QWidget:
        self._blades = _table(
            [
                "Machine",
                "Lab",
                "Status",
                "OS",
                "Free memory",
                "Free disk",
                "Rendering",
                "Finished",
                "Failed",
                "Notes",
                "GPU",
            ]
        )
        self._troubled = QtWidgets.QCheckBox("Only machines with problems")
        self._troubled.setChecked(False)
        self._troubled.toggled.connect(lambda _: self._fill_blades())
        self._in_windows = QtWidgets.QCheckBox("Only machines in Windows")
        self._in_windows.toggled.connect(lambda _: self._fill_blades())
        self._blades.currentItemChanged.connect(lambda *_: self._machine_chosen())
        self._blades.itemDoubleClicked.connect(lambda item, _: self._map(item))
        self._blades.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._blades.customContextMenuRequested.connect(self._machine_menu)
        self._lab_map: LabMapWindow | None = None
        self._machine_detail = QtWidgets.QTextBrowser()
        self._machine_detail.setPlaceholderText(
            "Choose a machine to see what failed on it."
        )
        self._machine_tries = TriesPanel(["Ended", "Job", "Task", "Problem"])
        side = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        side.addWidget(self._machine_detail)
        side.addWidget(self._machine_tries)
        side.setSizes([300, 460])
        split = QtWidgets.QSplitter()
        split.addWidget(self._blades)
        split.addWidget(side)
        split.setSizes([680, 600])
        hint = QtWidgets.QLabel(
            "Failed counts tasks from the last 12 hours that failed because of the "
            "machine itself, such as running out of memory or crashing. Machines in "
            "red failed tasks from several jobs, so retries avoid them. Double-click "
            "a machine to see where it is on its lab's map; right-click it for a "
            "fresh start or hyperfocus."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self._machine_download = widgets.button(
            "Download Logs",
            "download",
            "Save the Tractor logs of the tasks that failed on this machine to a "
            "folder you choose",
            clicked=self._download_machine_logs,
        )
        self._machine_download.setEnabled(False)
        self._span = QtWidgets.QComboBox()
        self._span.setToolTip(
            "How far back to list the machine's failed tries and download their "
            "logs. Tries from before a fresh start are listed too, in grey."
        )
        for label, seconds in _SPANS:
            self._span.addItem(label, seconds)
        self._span.setCurrentIndex(_saved_span())
        self._span.currentIndexChanged.connect(self._span_chosen)
        machine_bar = QtWidgets.QHBoxLayout()
        machine_bar.addWidget(self._troubled)
        machine_bar.addWidget(self._in_windows)
        machine_bar.addStretch(1)
        machine_bar.addWidget(QtWidgets.QLabel("Failed tries from"))
        machine_bar.addWidget(self._span)
        machine_bar.addWidget(self._machine_download)
        layout.addLayout(machine_bar)
        layout.addWidget(split, 1)
        layout.addWidget(hint)
        return page

    def _build_activity(self) -> QtWidgets.QWidget:
        self._activity = _table(
            ["When", "By", "Result", "Job", "Task", "Artist", "Problem", "Steered to"]
        )
        hint = QtWidgets.QLabel("Every retry, newest first.")
        hint.setToolTip(f"From the Farm Doctor's journal, {self._paths.journal}")
        hint.setEnabled(False)
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(self._activity, 1)
        layout.addWidget(hint)
        return page

    # ---- reading -------------------------------------------------------

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    def _read_all(
        self,
    ) -> _Found:
        """On a pool thread: the farm, the headless doctor's files, which
        machines are running Windows, and which were taken off the farm here."""
        checkup = self._doctor.check()
        return (
            checkup,
            records.read_status(self._paths),
            records.read_journal(self._paths),
            windows.running_windows([health.blade for health in checkup.blades]),
            records.read_nimbied(self._paths),
        )

    def _read(
        self,
        found: _Found,
    ) -> None:
        self._busy = False
        self._checkup, status, journal, self._windows, self._nimbied = found
        self._doctor_state = status
        self._show_doctor_status(status)
        self._follow_doctor_scope(status)
        self._fill_problems()
        self._fill_blades()
        self._fill_activity(journal)
        unreadable = len(self._checkup.problems)
        # What a right-click did, shown once with the list that reflects it.
        note, self._note = self._note, ""
        self._status.setText(
            (f"{note} · " if note else "")
            + f"updated {time.strftime('%H:%M:%S')}"
            + (f" · {unreadable} logs unreadable" if unreadable else "")
        )

    def _failed(self, exc: Exception) -> None:
        self._busy = False
        self._status.setText(str(exc))

    def _show_doctor_status(self, status: dict[str, Any] | None) -> None:
        host = str((status or {}).get("host", "")).split(".")[0]
        healthy = False
        if status is None:
            text = (
                "Automatic retries are off, so failed tasks wait until someone retries "
                "them."
            )
        elif status.get("stopped"):
            text = (
                f"Automatic retries stopped at {_clock(status['stopped'])}, so failed "
                "tasks wait until someone retries them."
            )
        elif not _running(status):
            text = (
                f"Automatic retries last checked the farm at "
                f"{_clock(status.get('checked'))}, so they may have stopped."
            )
        elif not status.get("acting"):
            text = (
                "Automatic retries are only watching: they note what they would retry, "
                f"but don't. Last checked at {_clock(status.get('checked'))}."
            )
        else:
            healthy = not status.get("error")
            text = (
                "Automatic retries are on: failed tasks that can work are retried, "
                "on machines that haven't failed them. Last checked at "
                f"{_clock(status.get('checked'))}."
            )
            if status.get("error"):
                text += f" The last check failed: {status['error']}"
        self._doctor_status.setText(text)
        self._doctor_status.set_kind(widgets.INFO if healthy else widgets.WARNING)
        self._doctor_status.setToolTip(
            f"The Farm Doctor runs on {host}, {status.get('login')}."
            if status and host
            else f"A TD starts them with {_RUN_COMMAND}"
        )

    def _follow_doctor_scope(self, status: dict[str, Any] | None) -> None:
        """Show the jobs the running doctor looks after, until the artist
        chooses otherwise."""
        if self._scope_chosen or status is None or not _running(status):
            return
        self._production.blockSignals(True)
        self._production.setChecked(not status.get("all_jobs"))
        self._production.blockSignals(False)

    def _choose_scope(self) -> None:
        self._scope_chosen = True

    # ---- filling -------------------------------------------------------

    def _plans(self) -> list[Plan]:
        if self._checkup is None:
            return []
        plans = self._checkup.plans(steer=True)
        if self._mine.isChecked():
            plans = [p for p in plans if p.diagnosis.job.owner == self._user]
        if self._production.isChecked():
            plans = [p for p in plans if p.diagnosis.job.sandwich]
        return plans

    def _fill_problems(self) -> None:
        chosen = {_item_key(item) for item in self._problems.selectedItems()}
        expanded = {
            _item_key(self._problems.topLevelItem(i))
            for i in range(self._problems.topLevelItemCount())
            if self._problems.topLevelItem(i).isExpanded()
        }
        self._problems.blockSignals(True)
        self._problems.clear()
        problems = group(self._plans())
        # One row per job; a job whose tasks failed in several ways lists each
        # way in rows beneath it, worst first.
        by_job: dict[int, list[Problem]] = {}
        for problem in problems:
            by_job.setdefault(problem.job.jid, []).append(problem)
        for found in by_job.values():
            kinds = _by_kind(found)
            if len(kinds) == 1:
                item = self._problem_row(kinds[0], kinds[0].job.title)
            else:
                merged = Problem(
                    kinds[0].job, tuple(plan for kind in kinds for plan in kind.plans)
                )
                item = self._problem_row(merged, kinds[0].job.title)
                item.setText(
                    _PROBLEM,
                    f"{item.text(_PROBLEM)} + {_count(len(kinds) - 1, 'more problem')}",
                )
                item.setData(0, _KINDS_ROLE, kinds)
                for kind in kinds:
                    item.addChild(self._problem_row(kind, ""))
            self._problems.addTopLevelItem(item)
            item.setExpanded(_item_key(item) in expanded)
            for row in (item, *(item.child(i) for i in range(item.childCount()))):
                row.setSelected(_item_key(row) in chosen)
        self._problems.blockSignals(False)
        for column in range(self._problems.columnCount()):
            self._problems.resizeColumnToContents(column)
        held = sum(len(p.plans) for p in problems if p.first.action is Action.HOLD)
        retrying = sum(len(p.plans) for p in problems) - held
        self._summary.setText(
            f"<span style='color:{_RED}'>{held} need a fix</span> · "
            f"<span style='color:{_GREEN}'>{retrying} will be retried</span>"
        )
        self._problem_chosen()

    def _fill_blades(self) -> None:
        current = self._blades.currentItem()
        chosen = current.text(0) if current is not None else None
        self._blades.blockSignals(True)
        self._blades.clear()
        if self._checkup is None:
            self._blades.blockSignals(False)
            return
        rows = sorted(
            self._checkup.blades,
            key=lambda h: (
                not h.suspect,
                -len(h.failures),
                not h.problems,
                h.blade.name,
            ),
        )
        for health in rows:
            if self._troubled.isChecked() and not health.problems:
                continue
            if self._in_windows.isChecked() and health.blade.name not in self._windows:
                continue
            blade = health.blade
            rendering = self._rendering(blade.name)
            item = widgets.SortableItem(
                [
                    blade.name,
                    labs.lab_of(blade.name),
                    _blade_state(health, rendering),
                    _system(health, self._windows),
                    f"{blade.free_memory_gb:.0f} GB",
                    f"{blade.free_disk_gb:.0f} GB",
                    str(rendering or ""),
                    str(health.passed or ""),
                    str(len(health.failures) or ""),
                    "; ".join(self._notes(health)),
                    blade.gpu.split(",")[0],
                ]
            )
            if health.suspect:
                for column in range(item.columnCount()):
                    item.setForeground(column, QtGui.QBrush(QtGui.QColor(_RED)))
            system = item.text(_OS_COLUMN)
            if color := _SYSTEM_COLORS.get(system):
                item.setForeground(_OS_COLUMN, QtGui.QBrush(QtGui.QColor(color)))
                item.setToolTip(_OS_COLUMN, _SYSTEM_TIPS[system])
                item.setToolTip(
                    0, "Flagged: it failed tasks from several jobs, so retries avoid it"
                )
            if health.failures:
                item.setToolTip(_FAILED_COLUMN, _failure_list(health))
            if blade.running != rendering:
                item.setToolTip(
                    _RENDERING_COLUMN,
                    f"Tractor's dashboard shows {blade.running} here, but "
                    f"{_count(rendering, 'task')} of any job is running: it still "
                    "counts commands it lost track of.",
                )
            if self._focus_until(blade.name):
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.INFO)))
            item.setData(0, _DATA_ROLE, health)
            self._blades.addTopLevelItem(item)
            if blade.name == chosen:
                self._blades.setCurrentItem(item)
        self._blades.blockSignals(False)
        for column in range(self._blades.columnCount()):
            self._blades.resizeColumnToContents(column)
        self._machine_chosen()

    def _machine_chosen(self) -> None:
        item = self._blades.currentItem()
        health: BladeHealth | None = item.data(0, _DATA_ROLE) if item else None
        failures = self._span_failures(health) if health else []
        self._machine_download.setEnabled(bool(failures))
        if item is None or health is None:
            self._machine_detail.clear()
            self._machine_tries.set_rows([])
            return
        self._machine_tries.set_rows(_machine_tries(health, failures))
        lost = frozenset(f.key for f in health.failures if f.cause == _LOST)
        cached = self._lost_reports.get(health.blade.name)
        report = cached[1] if cached and cached[0] == lost else None
        self._machine_detail.setHtml(
            _describe_machine(
                health,
                report,
                health.blade.name in self._windows,
                self._rendering(health.blade.name),
                self._focus_until(health.blade.name),
            )
        )
        if lost and report is None:
            background.run(
                lambda: _read_lost(health),
                lambda found: self._lost_read(health, lost, found),
                lambda exc: self._status.setText(str(exc)),
            )

    def _map(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """Open the lab map at the double-clicked machine's desk."""
        if self._lab_map is None:
            self._lab_map = LabMapWindow(self)
        self._lab_map.show_machine(item.text(0))
        self._lab_map.show()
        self._lab_map.raise_()
        self._lab_map.activateWindow()

    def _notes(self, health: BladeHealth) -> list[str]:
        notes = _machine_notes(health, self._focus_until(health.blade.name))
        taken = self._nimbied.get(health.blade.name)
        if (
            taken
            and health.blade.nimby
            and not health.blade.nimby.startswith("/local/")
        ):
            notes.insert(
                0,
                f"Taken off the farm by {taken.get('by')} at "
                f"{_clock(float(taken.get('when', 0)))}",
            )
        return notes

    def _focus_until(self, machine: str) -> float | None:
        """When `machine`'s hyperfocus ends, or None if it's not in one."""
        return self._checkup.focused.get(machine) if self._checkup else None

    def _machine_menu(self, position: QtCore.QPoint) -> None:
        item = self._blades.itemAt(position)
        if item is None:
            return
        self._blades.setCurrentItem(item)
        health: BladeHealth = item.data(0, _DATA_ROLE)
        menu = QtWidgets.QMenu(self)
        menu.setToolTipsVisible(True)
        view = menu.addAction("View on Map")
        view.triggered.connect(lambda: self._map(item))
        menu.addSeparator()
        self._machine_actions(menu, health)
        menu.exec_(self._blades.viewport().mapToGlobal(position))

    def _renderers_only(self, title: str, question: str) -> bool:
        """Ask `question`, warning that it's for the production's renderers:
        anyone may, but it changes where everyone's retries go."""
        answer = QtWidgets.QMessageBox.warning(
            self,
            title,
            f"{question}\n\nDon't do this unless you're one of the production's "
            "dedicated renderers. It changes where the automatic retries send "
            "everyone's tasks, not just yours.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _machine_actions(self, menu: QtWidgets.QMenu, health: BladeHealth) -> None:
        """What may be done to a machine: fresh starts and hyperfocus by anyone,
        after a warning, and nimbying by anyone Tractor allows."""
        machine = health.blade.name
        fresh = menu.addAction("Fresh Start")
        fresh.setToolTip(
            "Stop counting its failures so far, as after rebooting it: it's no "
            "longer flagged, and retries stop avoiding it."
        )
        fresh.setEnabled(bool(health.failures))
        fresh.triggered.connect(self._fresh_start)
        if self._focus_until(machine):
            focus = menu.addAction("Stop Hyperfocus")
            focus.triggered.connect(lambda: self._set_focus(machine, stop=True))
        else:
            focus = menu.addAction("Hyperfocus")
            focus.setToolTip(
                "Test it: while it's free, retries go to it before any other "
                "machine, one at a time, for the next "
                f"{records.FOCUS_SECONDS // 3600} hours."
            )
            focus.triggered.connect(lambda: self._set_focus(machine))
        menu.addSeparator()
        nimby = health.blade.nimby
        if machine in self._nimbied and nimby and not nimby.startswith("/local/"):
            back = menu.addAction("Put Back on the Farm")
            back.setToolTip("Un-nimby it, so Tractor sends it work again")
            back.triggered.connect(lambda: self._set_nimby(health, back_on=True))
        elif nimby:
            busy = menu.addAction("Put Back on the Farm")
            busy.setEnabled(False)
            busy.setToolTip(
                "Someone is using it, so it took itself off the farm; it goes back "
                "on by itself when they're done."
                if nimby.startswith("/local/")
                else "It was taken off the farm outside the Farm Doctor, so it's left "
                "as it is."
            )
        else:
            off = menu.addAction("Take Off the Farm (Nimby)")
            off.setToolTip("Nimby it, so Tractor sends it no new work")
            off.triggered.connect(lambda: self._set_nimby(health, back_on=False))

    def _set_focus(self, machine: str, stop: bool = False) -> None:
        """Put `machine` in hyperfocus, or with `stop`, take it out."""
        if not stop:
            status = self._doctor_state
            acting = bool(status and _running(status) and status.get("acting"))
            # Only the account running them can see how they run.
            caveat = (
                "\n\nThe automatic retries aren't retrying for real now, so "
                "nothing is sent to it until they are."
                if status is not None and not acting
                else ""
            )
            if not self._renderers_only(
                "Hyperfocus",
                f"Hyperfocus on {machine}?\n\nWhile it's free, the automatic "
                "retries send it one task at a time, before any other machine, and "
                "stop avoiding it, so you can see whether it still fails. It ends "
                f"by itself in {records.FOCUS_SECONDS // 3600} hours. A task sent "
                "to it that can't start there within "
                f"{PIN_SECONDS // 60} minutes is freed to run anywhere.{caveat}",
            ):
                return
        try:
            records.focus(self._paths, machine, getpass.getuser(), stop)
        except OSError as exc:
            self._status.setText(f"Couldn't change {machine}'s hyperfocus: {exc}")
            return
        self._status.setText(
            f"Stopped hyperfocus on {machine}."
            if stop
            else f"Hyperfocus on {machine}: from the doctor's next round, retries "
            "go to it first while it's free."
        )
        self.refresh()

    def _rendering(self, machine: str) -> int:
        """How many tasks are really running on `machine`."""
        return self._checkup.rendering.get(machine, 0) if self._checkup else 0

    def _span_failures(self, health: BladeHealth) -> list[Failure]:
        """The failures the machine was blamed for within the chosen span,
        whether or not they still count against it, oldest first."""
        if self._checkup is None:
            return []
        since = time.time() - int(self._span.currentData())
        return [
            f
            for f in self._checkup.history
            if f.blade == health.blade.name and f.blames_blade and f.when >= since
        ]

    def _span_chosen(self) -> None:
        QtCore.QSettings(*_SETTINGS).setValue(_SPAN_KEY, self._span.currentIndex())
        self._machine_chosen()

    def _set_nimby(self, health: BladeHealth, back_on: bool) -> None:
        """Take the machine off the farm, or with `back_on`, put it back: only
        one taken off from here, as `_machine_actions` offers. Tractor refuses
        it to a login without the right."""
        machine, address = health.blade.name, health.blade.address
        if not back_on:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Take Off the Farm",
                f"Take {machine} off the farm?\n\nTractor nimbys it, so it gets no "
                "new work until it's put back, from this menu.",
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        doing = "Putting" if back_on else "Taking"
        self._status.setText(
            f"{doing} {machine} {'back on' if back_on else 'off'} the farm…"
        )

        def change() -> None:
            connection = self._connect()
            if back_on:
                connection.unnimby(machine, address)
            else:
                connection.nimby(machine, address)
            records.note_nimby(self._paths, machine, self._user, undo=back_on)

        background.run(
            change, lambda _: self._nimby_changed(machine, back_on), self._failed
        )

    def _nimby_changed(self, machine: str, back_on: bool) -> None:
        self._status.setText(
            f"Put {machine} back on the farm."
            if back_on
            else f"Took {machine} off the farm."
        )
        self.refresh()

    def _fresh_start(self) -> None:
        """Give the chosen machine a fresh start, for the doctor and this window."""
        item = self._blades.currentItem()
        if item is None:
            return
        machine = item.text(0)
        if not self._renderers_only(
            "Fresh Start",
            f"Give {machine} a fresh start?\n\nIts failures so far stop counting, "
            "so it's no longer flagged and retries stop avoiding it. Use this "
            "once it's fixed, as after rebooting it. If it fails tasks again, it "
            "will be flagged again.",
        ):
            return
        try:
            records.clear(self._paths, machine, getpass.getuser())
        except OSError as exc:
            self._status.setText(f"Couldn't give {machine} a fresh start: {exc}")
            return
        self._status.setText(
            f"Gave {machine} a fresh start. The doctor stops avoiding it from its "
            "next round."
        )
        self.refresh()

    def _lost_read(
        self, health: BladeHealth, lost: frozenset[str], report: _LostReport
    ) -> None:
        self._lost_reports[health.blade.name] = (lost, report)
        item = self._blades.currentItem()
        if item is not None and item.text(0) == health.blade.name:
            self._machine_detail.setHtml(
                _describe_machine(
                    health,
                    report,
                    health.blade.name in self._windows,
                    self._rendering(health.blade.name),
                    self._focus_until(health.blade.name),
                )
            )

    def _fill_activity(self, journal: list[dict[str, Any]]) -> None:
        self._activity.clear()
        for entry in reversed(journal):
            services = entry.get("services") or []
            result = str(entry.get("result", ""))
            item = widgets.SortableItem(
                [
                    time.strftime("%b %d %H:%M", time.localtime(entry.get("when", 0))),
                    _who(str(entry.get("by", "doctor"))),
                    _result_words(result),
                    f"{entry.get('jid')}  {entry.get('job', '')}",
                    str(entry.get("task", "")),
                    str(entry.get("owner", "")),
                    _cause_label(_KEY_OF_LABEL.get(str(entry.get("cause", "")), "")),
                    _kept_off(" ".join(str(s.get("after")) for s in services)),
                ]
            )
            item.setData(0, widgets.SORT_ROLE, float(entry.get("when", 0)))
            color = (
                _GREEN
                if result == "retried"
                else _RED
                if result.startswith("failed")
                else _GREY
            )
            item.setForeground(2, QtGui.QBrush(QtGui.QColor(color)))
            item.setToolTip(2, result)
            if services:
                item.setToolTip(7, "; ".join(str(s.get("after")) for s in services))
            self._activity.addTopLevelItem(item)
        for column in range(self._activity.columnCount()):
            self._activity.resizeColumnToContents(column)

    def _problem_row(self, problem: Problem, title: str) -> QtWidgets.QTreeWidgetItem:
        """A row for `problem`, titled with its job's `title`, or untitled as
        one of a job's several ways of failing."""
        first = problem.first
        d = first.diagnosis
        when = f" at {_clock(first.retry_at)}" if first.action is Action.WAIT else ""
        job = problem.job
        item = widgets.SortableItem(
            [
                f"{job.jid}  {title}" if title else "",
                (job.shot or "—") if title else "",
                "",
                job.owner if title else "",
                str(len(problem.plans)),
                _cause_label(d.latest.cause.key),
                _ACTION_WORDS[first.action] + when,
                str(max(p.diagnosis.attempts for p in problem.plans)),
                ", ".join(d.same_way_blades[-4:]),
            ]
        )
        item.setData(0, _DATA_ROLE, problem)
        kind = f"{d.latest.cause.key}/{first.action.name}"
        item.setData(0, _KEY_ROLE, (job.jid, "" if title else kind))
        if title:
            # The job's, so once on its row and not on each way it failed.
            job_status.show(item, _STATUS, job)
        item.setForeground(
            _NEXT, QtGui.QBrush(QtGui.QColor(_ACTION_COLORS[first.action]))
        )
        item.setToolTip(_PROBLEM, _why(d))
        return item

    def _problem_chosen(self) -> None:
        problems = self._selected_problems()
        items = self._problems.selectedItems()
        kinds: list[Problem] | None = items[0].data(0, _KINDS_ROLE) if items else None
        if kinds:
            self._detail.setHtml("<hr>".join(_describe(kind) for kind in kinds))
        else:
            self._detail.setHtml(_describe(problems[0]) if problems else "")
        self._problem_tries.set_rows(_problem_tries(problems[0]) if problems else [])
        self._enable()

    # ---- acting --------------------------------------------------------

    def _retry_selected(self) -> None:
        plans = self._selected_plans()
        if not plans:
            return
        held = [p for p in plans if p.action is Action.HOLD]
        if held and not self._confirm_held(held):
            return
        settings = Settings(acting=True)
        self._status.setText(f"Retrying {_count(len(plans), 'task')}…")
        self._retry.setEnabled(False)
        background.run(
            lambda: self._doctor.retry(
                plans,
                settings,
                self._connect,
                self._paths,
                by=f"{self._user} (Farm Doctor tab)",
            ),
            self._retried,
            self._failed,
        )

    def _connect(self) -> Connection:
        def keyring() -> str | None:
            # The Farm tab's remembered login, if it is this artist's own.
            remembered = remembered_login()
            return remembered[1] if remembered and remembered[0] == self._user else None

        try:
            return login.connect(self._paths, self._user, keyring)
        except NoLogin as exc:
            raise NoLogin(
                "This needs your Tractor login. Retry any task once on a shot's "
                "Farm tab with Remember ticked, then try again here."
            ) from exc

    def _retried(self, entries: list[dict[str, Any]]) -> None:
        retried = sum(1 for e in entries if e.get("result") == "retried")
        failed = [e for e in entries if e.get("result") != "retried"]
        text = f"Retried {_count(retried, 'task')}."
        if failed:
            text += f" {len(failed)} not: {_result_words(str(failed[0].get('result')))}"
        self._status.setText(text)
        self._enable()
        QtCore.QTimer.singleShot(3000, self.refresh)

    def _confirm_held(self, held: list[Plan]) -> bool:
        reasons = sorted({_why(p.diagnosis) for p in held})
        answer = QtWidgets.QMessageBox.question(
            self,
            "Retry Anyway",
            f"Retrying probably won't fix {len(held)} of these tasks:\n\n"
            + "\n".join(f"• {r}" for r in reasons[:4])
            + "\n\nRetry them anyway?",
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _job_acted(self, note: str) -> None:
        self._note = note
        self.refresh()

    def _open_job(self) -> None:
        problems = self._selected_problems()
        if not problems:
            return
        job = problems[0].job
        shot = job.shot
        if shot is None:
            self._status.setText(
                f"Job {job.jid} names no shot this window knows, so it can't be opened here."
            )
            return
        self.open_job.emit(shot, job.jid)

    def _download_problem_logs(self) -> None:
        wanted = [
            _LogRequest(
                p.diagnosis.job.owner,
                p.diagnosis.job.jid,
                p.diagnosis.task.tid,
                p.diagnosis.task.title,
            )
            for p in self._selected_plans()
        ]
        jobs = sorted({request.jid for request in wanted})
        name = f"J{jobs[0]}" if len(jobs) == 1 else f"{len(jobs)}_jobs"
        title = ", ".join(f"job {jid}" for jid in jobs)
        self._download_logs(wanted, name, f"Tractor logs of {title}")

    def _download_machine_logs(self) -> None:
        item = self._blades.currentItem()
        if item is None:
            return
        health: BladeHealth = item.data(0, _DATA_ROLE)
        wanted = []
        for failure in self._span_failures(health):
            if match := _ATTEMPT_KEY.match(failure.key):
                wanted.append(
                    _LogRequest(
                        failure.owner, int(match[1]), int(match[2]), failure.task
                    )
                )
        name = health.blade.name
        self._download_logs(
            wanted, name, f"Tractor logs of the tasks that failed on {name}"
        )

    def _download_logs(self, wanted: list[_LogRequest], name: str, title: str) -> None:
        """Ask where to save, then write every task's Tractor log into one text
        file, offered as `name` and today's date."""
        wanted = list(dict.fromkeys(wanted))
        if not wanted:
            return
        settings = QtCore.QSettings(*_SETTINGS)
        folder = Path(str(settings.value(_LOG_FOLDER_KEY, "") or Path.home()))
        suggested = folder / f"{_UNSAFE.sub('_', name)}_{time.strftime('%Y-%m-%d')}.txt"
        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            f"Save {_count(len(wanted), 'Tractor log')} as",
            str(suggested),
            "Text files (*.txt);;All files (*)",
        )
        if not chosen:
            return
        target = Path(chosen)
        settings.setValue(_LOG_FOLDER_KEY, str(target.parent))
        self._status.setText(f"Downloading {_count(len(wanted), 'log')}…")
        background.run(
            lambda: _save_logs(target, wanted, title),
            lambda found: self._logs_saved(target, *found),
            self._failed,
        )

    def _logs_saved(self, target: Path, saved: int, problems: list[str]) -> None:
        text = f"Saved {_count(saved, 'log')} to {target}."
        if problems:
            text += f" {len(problems)} couldn't be read: {problems[0]}"
        self._status.setText(text)
        # The label is narrow; the file's full path is worth seeing.
        self._status.setToolTip(text)

    def _selected_problems(self) -> list[Problem]:
        return [item.data(0, _DATA_ROLE) for item in self._problems.selectedItems()]

    def _selected_plans(self) -> list[Plan]:
        """The selected rows' tasks, each once: a job's row and a row beneath
        it may both be selected."""
        plans: dict[tuple[int, int], Plan] = {}
        for problem in self._selected_problems():
            for plan in problem.plans:
                d = plan.diagnosis
                plans.setdefault((d.job.jid, d.task.tid), plan)
        return list(plans.values())

    def _enable(self) -> None:
        chosen = bool(self._problems.selectedItems())
        self._retry.setEnabled(chosen)
        self._download.setEnabled(chosen)
        self._open.setEnabled(chosen)


def _describe(problem: Problem) -> str:
    """The problem's diagnosis, as HTML for the detail pane."""
    first = problem.first
    d = first.diagnosis
    latest = d.latest
    label, advice = _cause_words(latest.cause.key)
    color = _ACTION_COLORS[first.action]
    why = _why(d)
    extra = "" if why == advice else f"<p>{html.escape(why)}</p>"
    tasks = ", ".join(html.escape(p.diagnosis.task.title) for p in problem.plans[:30])
    if len(problem.plans) > 30:
        tasks += f" and {len(problem.plans) - 30} more"
    return (
        f"<h3>{problem.job.jid} {html.escape(problem.job.title)}</h3>"
        f"<p><b>{html.escape(label)}.</b> {html.escape(advice)}</p>{extra}"
        f"<p><b style='color:{color}'>{_next_step(first)}</b></p>"
        f"<p><b>From the log</b> of {html.escape(d.task.title)} (try "
        f"{latest.attempt.number}, on {html.escape(latest.attempt.blade)}):</p>"
        f"<pre style='white-space: pre-wrap'>{html.escape(_evidence(d))}</pre>"
        f"<p><b>Failed this way on:</b> {html.escape(', '.join(d.same_way_blades))}</p>"
        f"<p><b>{_count(len(problem.plans), 'task')}:</b> {tasks}</p>"
        "<p>Their failed tries are listed below, latest first: click one to read "
        "its log.</p>"
    )


def _cause_words(key: str) -> tuple[str, str]:
    return _CAUSE_WORDS.get(key, _CAUSE_WORDS["unknown"])


def _cause_label(key: str) -> str:
    return _cause_words(key)[0] if key else ""


def _why(d: Diagnosis) -> str:
    """Why the task got its verdict, in an artist's words."""
    cause = d.latest.cause
    advice = _cause_words(cause.key)[1]
    if d.verdict is not diagnose.Verdict.PERSON or cause.remedy is Remedy.PERSON:
        return advice
    if cause.blade_limit is not None and len(d.same_way_blades) >= cause.blade_limit:
        return (
            f"It failed the same way on {len(d.same_way_blades)} different machines, "
            "so the problem is in the task, not the machines."
        )
    if d.attempts >= MAX_ATTEMPTS:
        return f"It's still failing after {d.attempts} tries."
    return advice


def _next_step(plan: Plan) -> str:
    """What happens to the task next, in a sentence."""
    if plan.action is Action.HOLD:
        if plan.diagnosis.verdict is not diagnose.Verdict.PERSON:
            return "Its job is paused, so it won't be retried."
        return "Needs a fix: retrying won't help until someone fixes it."
    if not plan.steers:
        where = "on any machine"
    elif plan.needs_memory:
        where = "on a machine with more free memory"
    else:
        where = "on a different machine"
    if plan.avoid:
        where += f", not {_either(plan.avoid)}"
    if plan.action is Action.WAIT:
        return f"Will be retried at {_clock(plan.retry_at)}, {where}."
    return f"Will be retried now, {where}."


def _evidence(d: Diagnosis) -> str:
    """The log line that shows the cause, or what the exit code means."""
    latest = d.latest
    if latest.attempt.number and latest.evidence.startswith(
        ("Exit code", "The log stops")
    ):
        if latest.cause.key == "lost":
            return (
                f"Exit code {latest.exit_code}: the machine restarted, and Tractor "
                "lost track of the task."
            )
        if latest.cause.key == "stopped":
            return f"Exit code {latest.exit_code}: Tractor stopped the task."
    return latest.evidence


def _running(status: dict[str, Any]) -> bool:
    """The automatic retries whose `status` this is are still checking the farm."""
    checked = float(status.get("checked") or 0)
    return not status.get("stopped") and time.time() - checked <= _STALE_SECONDS


def _system(health: BladeHealth, running_windows: set[str]) -> str:
    """Which system the machine is in: Windows, Linux, or unknown while offline."""
    if health.blade.name in running_windows:
        return _WINDOWS
    return "—" if _offline_for(health) is not None else _LINUX


def _blade_state(health: BladeHealth, rendering: int) -> str:
    blade = health.blade
    if _offline_for(health) is not None:
        return "Offline"
    if blade.nimby.startswith("/local/"):
        return "In Use (Nimby)"
    if blade.nimby:
        return "Off the farm (Nimby)"
    return "Rendering" if rendering else "Available"


def _machine_notes(health: BladeHealth, focus_until: float | None = None) -> list[str]:
    notes = []
    if focus_until:
        notes.append(f"Hyperfocus until {_clock(focus_until)}: retries go here first")
    if (offline := _offline_for(health)) is not None:
        notes.append(f"Offline for {_ago(offline)}")
    if health.blade.free_disk_gb < LOW_DISK_GB:
        notes.append(f"Only {health.blade.free_disk_gb:.0f} GB of disk space free")
    if health.cleared and time.time() - health.cleared < WINDOW_SECONDS:
        when = time.strftime("%b %d %H:%M", time.localtime(health.cleared))
        notes.append(f"Fresh start {when}; only failures since count")
    if health.failures:
        counts: dict[str, int] = {}
        for failure in health.failures:
            label = _failure_label(failure).lower()
            counts[label] = counts.get(label, 0) + 1
        listed = ", ".join(f"{label} ×{n}" for label, n in counts.items())
        notes.append(
            f"Failed {_count(len(health.failures), 'task')} from "
            f"{_count(health.jobs, 'job')}: {listed}"
        )
    return notes


def _offline_for(health: BladeHealth) -> float | None:
    """How long the machine has been silent, or None while it reports in."""
    heartbeat = health.blade.heartbeat
    silent = time.time() - heartbeat if heartbeat is not None else 0.0
    return silent if silent > SILENT_SECONDS else None


@dataclass(frozen=True)
class _LogRequest:
    """A task whose Tractor log to download."""

    owner: str
    jid: int
    tid: int
    title: str


def _save_logs(
    target: Path, wanted: list[_LogRequest], title: str
) -> tuple[int, list[str]]:
    """Write every task's Tractor log into the one text file `target`, each
    under a heading naming its job and task. Returns how many were saved and
    why the rest weren't.

    Raises:
        FarmError: The file couldn't be written.
    """
    rule = "#" * 80
    parts = [f"{title}\nDownloaded {time.strftime('%Y-%m-%d %H:%M')}\n"]
    saved, problems = 0, []
    for request in wanted:
        try:
            text = tractor.task_log(
                request.owner, request.jid, request.tid, request.title
            )
        except tractor.FarmError as exc:
            problems.append(str(exc))
            continue
        parts.append(
            f"\n{rule}\n# Job {request.jid}, task {request.tid}: {request.title} "
            f"({request.owner})\n{rule}\n\n{text.rstrip()}\n"
        )
        saved += 1
    try:
        target.write_text("".join(parts), encoding="utf-8")
    except OSError as exc:
        raise tractor.FarmError(f"Could not write {target} ({exc}).") from exc
    return saved, problems


def _describe_machine(
    health: BladeHealth,
    report: _LostReport | None,
    in_windows: bool,
    rendering: int,
    focus_until: float | None = None,
) -> str:
    """A machine's health and what failed on it, as HTML for the detail pane.
    `report` says what its lost tasks' logs show, None while they're read.
    `rendering` is how many tasks really run on it."""
    blade = health.blade
    flagged = (
        f"<p><b style='color:{_RED}'>Flagged:</b> it failed tasks from "
        f"{_count(health.jobs, 'job')}, so retries avoid it.</p>"
        if health.suspect
        else ""
    )
    reported = (
        f"{_ago(time.time() - blade.heartbeat)} ago" if blade.heartbeat else "never"
    )
    specs = (
        f"{html.escape(labs.lab_of(blade.name))} · {html.escape(blade.profile)} · "
        f"{blade.cpus} CPUs · "
        f"{html.escape(blade.gpu.split(',')[0] or 'no GPU')}<br>"
        f"{blade.free_memory_gb:.0f} GB memory and {blade.free_disk_gb:.0f} GB disk "
        f"free · rendering {rendering} · last reported {reported}"
    )
    # The failures get their own section below.
    notes = "".join(
        f"<li>{html.escape(n)}</li>"
        for n in _machine_notes(health, focus_until)
        if not n.startswith("Failed ")
    )
    head = (
        f"<h3>{html.escape(blade.name)} — {html.escape(_blade_state(health, rendering))}"
        f"{' · in Windows' if in_windows else ''}</h3>"
        f"{flagged}<p>{specs}</p>" + (f"<ul>{notes}</ul>" if notes else "")
    )
    if not health.failures:
        since = (
            "since its fresh start"
            if health.cleared and time.time() - health.cleared < WINDOW_SECONDS
            else "in the last 12 hours"
        )
        return head + (
            f"<p>Nothing has failed here because of the machine {since}. Earlier "
            "failed tries are listed below, by the time chosen above.</p>"
        )
    tally: dict[str, int] = {}
    lines: dict[str, int] = {}
    jobs: dict[int, list[Failure]] = {}
    for failure in health.failures:
        label = _failure_label(failure)
        tally[label] = tally.get(label, 0) + 1
        line = _machine_evidence(failure)
        lines[line] = lines.get(line, 0) + 1
        jobs.setdefault(failure.jid, []).append(failure)
    tallied = " · ".join(f"{html.escape(k)} ×{n}" for k, n in tally.items())
    hit = "".join(
        f"<li>Job {jid} ({html.escape(found[0].owner)}): {_count(len(found), 'task')}</li>"
        for jid, found in sorted(jobs.items(), key=lambda j: -len(j[1]))
    )
    logged = "".join(
        f"<li><code>{html.escape(line)}</code>" + (f" ×{n}" if n > 1 else "") + "</li>"
        for line, n in sorted(lines.items(), key=lambda kv: -kv[1])[:6]
    )
    return head + (
        f"<p><b>Failed here in the last 12 hours</b> "
        f"({_count(len(health.failures), 'task')}): {tallied}</p>"
        f"<p><b>Jobs hit:</b></p><ul>{hit}</ul>"
        f"{_why_lost(health, report)}"
        f"<p><b>From the logs:</b></p><ul>{logged}</ul>"
        "<p>Its failed tasks are listed below, latest first: click one to read "
        "its log.</p>"
    )


def _problem_tries(problem: Problem) -> list[TryRow]:
    """Every failed try of the problem's tasks, latest first."""
    rows: list[tuple[float, TryRow]] = []
    for plan in problem.plans:
        d = plan.diagnosis
        for f in d.findings:
            rows.append(
                (
                    f.attempt.ended or f.attempt.started,
                    TryRow(
                        cells=(
                            _clock(f.attempt.ended or f.attempt.started),
                            d.task.title,
                            str(f.attempt.number or ""),
                            f.attempt.blade,
                            _cause_label(f.cause.key),
                        ),
                        owner=d.job.owner,
                        jid=d.job.jid,
                        tid=d.task.tid,
                        title=d.task.title,
                        key=f.attempt.key if f.attempt.number else "",
                        machine=f.attempt.blade,
                        when=f.attempt.ended or f.attempt.started,
                    ),
                )
            )
    return [row for _, row in sorted(rows, key=lambda r: r[0], reverse=True)]


def _machine_tries(health: BladeHealth, failures: list[Failure]) -> list[TryRow]:
    """The tries in `failures`, latest first; those from before the machine's
    fresh start in grey."""
    rows = []
    for f in reversed(failures):
        match = _ATTEMPT_KEY.match(f.key)
        if match is None:
            continue
        before = health.cleared is not None and f.when < health.cleared
        problem = _failure_label(f) + (" (before fresh start)" if before else "")
        rows.append(
            TryRow(
                cells=(_when(f.when), str(f.jid), f.task, problem),
                owner=f.owner,
                jid=int(match[1]),
                tid=int(match[2]),
                title=f.task,
                key=f.key,
                machine=health.blade.name,
                when=f.when,
                color=theme.DISABLED_TEXT if before else None,
            )
        )
    return rows


def _when(seconds: float) -> str:
    """A time, with its day when it isn't today."""
    if time.strftime("%x", time.localtime(seconds)) == time.strftime("%x"):
        return _clock(seconds)
    return time.strftime("%b %d %H:%M", time.localtime(seconds))


def _saved_span() -> int:
    try:
        saved = QtCore.QSettings(*_SETTINGS).value(_SPAN_KEY, _DEFAULT_SPAN)
        index = int(saved)
    except (TypeError, ValueError):
        return _DEFAULT_SPAN
    return index if 0 <= index < len(_SPANS) else _DEFAULT_SPAN


@dataclass(frozen=True)
class _Lost:
    """A task a machine lost, as its log shows it."""

    started: float
    # What it was doing when it was lost, in a few words.
    doing: str


@dataclass(frozen=True)
class _LostReport:
    lost: tuple[_Lost, ...]
    # When the machine last booted, if Tractor says.
    booted: float | None


def _read_lost(health: BladeHealth) -> _LostReport:
    """On a pool thread: what the logs of the machine's lost tasks show."""
    wanted = {f.key for f in health.failures if f.cause == _LOST}
    tasks = {
        (f.owner, int(m[1]), int(m[2]))
        for f in health.failures
        if f.key in wanted and (m := _ATTEMPT_KEY.match(f.key))
    }
    lost = []
    for owner, jid, tid in tasks:
        try:
            found = attempts.parse(tractor.task_log(owner, jid, tid))
        except tractor.FarmError:
            continue
        lost += [_Lost(a.started, _doing(a.text)) for a in found if a.key in wanted]
    try:
        booted = tractor.blade_booted(health.blade.name)
    except tractor.FarmError:
        booted = None
    return _LostReport(tuple(sorted(lost, key=lambda x: x.started)), booted)


def _doing(text: str) -> str:
    """What a cut-off attempt was doing, from the end of its output."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("+")]
    last = lines[-1] if lines else ""
    if "[denoise]" in last:
        return "denoising"
    timers = _HUSK_TIMER.findall(text)
    if timers:
        minutes, seconds = timers[-1]
        if int(minutes) == 0 and int(seconds) < 30:
            return "just starting to render"
        return f"rendering, {minutes}:{seconds} in"
    if "husk" in text:
        return "loading the scene to render"
    if "hou." in text or "hython" in text:
        return "cooking in Houdini"
    return "running"


def _why_lost(health: BladeHealth, report: _LostReport | None) -> str:
    """Why the machine lost its tasks, as far as their logs and Tractor show."""
    count = sum(1 for f in health.failures if f.cause == _LOST)
    if not count:
        return ""
    heading = (
        f"<p><b>Why it lost {_count(count, 'task')}:</b> exit code 223 means "
        "Tractor's service on the machine stopped while a task was running. When the "
        "service came back, it couldn't tell whether the task had finished, so it "
        "marked it failed.</p>"
    )
    if report is None:
        return heading + "<p><i>Reading the lost tasks' logs…</i></p>"
    points = []
    starts = [x.started for x in report.lost]
    gaps = sorted(b - a for a, b in zip(starts, starts[1:]) if b > a)
    if len(starts) >= 3 and gaps and gaps[len(gaps) // 2] < 10 * 60:
        points.append(
            f"It lost {len(starts)} tasks between {_clock(starts[0])} and "
            f"{_clock(starts[-1])}, taking on a new one about every "
            f"{max(gaps[len(gaps) // 2] / 60, 1):.0f} min. So the service kept "
            "restarting, rather than crashing once."
        )
    doing: dict[str, int] = {}
    for x in report.lost:
        kind = "rendering" if x.doing.startswith("rendering,") else x.doing
        doing[kind] = doing.get(kind, 0) + 1
    if doing:
        listed = ", ".join(
            f"{n} {kind}" for kind, n in sorted(doing.items(), key=lambda kv: -kv[1])
        )
        points.append(f"When they were lost: {listed}.")
    jobs = {f.jid for f in health.failures if f.cause == _LOST}
    kinds = {f.task.split(" ")[0] for f in health.failures if f.cause == _LOST}
    if len(jobs) > 1 or len(kinds) > 1:
        points.append(
            f"They were {' and '.join(sorted(k.lower() for k in kinds))} tasks from "
            f"{_count(len(jobs), 'job')}, so the cause is the machine, not a scene."
        )
    last = max(f.when for f in health.failures if f.cause == _LOST)
    started = health.blade.service_started
    if report.booted and report.booted > last:
        points.append(
            f"The machine rebooted at {_clock(report.booted)}, after its last lost "
            "task, and has lost none since."
        )
    elif started and started > last:
        points.append(f"Tractor's service on it last restarted at {_clock(started)}.")
    elif report.booted:
        points.append(
            f"It hasn't rebooted since then (last booted {_clock(report.booted)})."
        )
    top = max(doing, key=lambda k: doing[k]) if doing else ""
    if top == "denoising":
        likely = (
            "Most likely, denoising ran the machine out of memory, and the system "
            "stopped Tractor's service along with it."
        )
    else:
        likely = (
            "Most likely, Tractor's service on this machine kept crashing or being "
            "stopped, often from running out of memory as a task loads. A reboot "
            "usually clears it; if it comes back, report the machine to the lab admins."
        )
    items = "".join(f"<li>{html.escape(point)}</li>" for point in points)
    return (
        heading
        + (f"<ul>{items}</ul>" if items else "")
        + f"<p>{html.escape(likely)}</p>"
    )


def _machine_evidence(failure: Failure) -> str:
    """The failure's log line, or what its exit code means."""
    if failure.cause == "lost" and failure.evidence.startswith(
        ("Exit code", "The log")
    ):
        return "The machine restarted, and Tractor lost track of the task."
    return failure.evidence


def _failure_list(health: BladeHealth) -> str:
    lines = [
        f"{_clock(f.when)}  J{f.jid} {f.task}: {_failure_label(f)}"
        for f in reversed(health.failures[-15:])
    ]
    return "\n".join(lines)


def _failure_label(failure: Failure) -> str:
    return _cause_label(failure.cause)


def _who(by: str) -> str:
    """Who retried: "Automatic" for the Farm Doctor, else the artist."""
    return "Automatic" if by == "doctor" else by.split(" (")[0]


def _result_words(result: str) -> str:
    if result == "retried":
        return "Retried"
    if result == "would retry (watching only)":
        return "Would retry (only watching)"
    if result.startswith("would retry ("):
        return "Not retried: no Tractor login"
    if result.startswith("skipped: it is "):
        state = result.removeprefix("skipped: it is ").split(" now")[0]
        return f"Skipped: already {state}"
    if result.startswith("failed: "):
        return "Failed: " + result.removeprefix("failed: ")
    if result == "freed":
        return "Freed: it couldn't start on its hyperfocus machine"
    return result.capitalize()


def _kept_off(services: str) -> str:
    """What a retry's service keys asked for, in words."""
    machines = list(dict.fromkeys(_AVOIDED.findall(services)))
    memory = [int(m) for m in _MEMORY.findall(services)]
    pinned = list(dict.fromkeys(_PINNED.findall(services)))
    parts = [f"Only on {_either(tuple(pinned))} (hyperfocus)"] if pinned else []
    if machines:
        parts.append(f"Not on {_either(tuple(machines))}")
    if memory:
        parts.append(f"needs {max(memory)} GB free memory")
    return "; ".join(parts)


def _either(names: tuple[str, ...]) -> str:
    """'a', 'a or b', 'a, b or c', or 'a, b, c or 2 others'."""
    shown = list(names[-3:])
    if len(names) > 3:
        return f"{', '.join(shown)} or {len(names) - 3} others"
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} or {shown[-1]}"


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} days"


def _by_kind(problems: list[Problem]) -> list[Problem]:
    """One job's `problems`, merged by what failed them and what comes next:
    the same failure worded a little differently, or due to be retried at
    another time, is one kind to a person. The soonest retried leads each."""
    kinds: dict[tuple[str, Action], list[Plan]] = {}
    for problem in problems:
        for plan in problem.plans:
            key = (plan.diagnosis.latest.cause.key, plan.action)
            kinds.setdefault(key, []).append(plan)
    return [
        Problem(problems[0].job, tuple(sorted(plans, key=lambda p: p.retry_at or 0.0)))
        for plans in kinds.values()
    ]


def _item_key(item: QtWidgets.QTreeWidgetItem) -> tuple[int, str]:
    """What a Problems row stands for, to find it again once refreshed: its
    job, and for a row beneath a job, how its tasks failed."""
    return item.data(0, _KEY_ROLE)


def _clock(when: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(when)) if when else "?"


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _table(headers: list[str]) -> QtWidgets.QTreeWidget:
    table = QtWidgets.QTreeWidget()
    widgets.sortable(table)
    table.setHeaderLabels(headers)
    table.setRootIsDecorated(False)
    table.setUniformRowHeights(True)
    table.setAlternatingRowColors(True)
    return table
