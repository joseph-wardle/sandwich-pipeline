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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render import farm
from pipe.core.render.doctor import login, records
from pipe.core.render.doctor import attempts, diagnose, windows
from pipe.core.render.doctor.blades import (
    LOW_DISK_GB,
    SILENT_SECONDS,
    BladeHealth,
    Failure,
    History,
)
from pipe.core.render.doctor.checkup import Checkup, Doctor, Problem, Settings, group
from pipe.core.render.doctor.diagnose import MAX_ATTEMPTS, Cause, Diagnosis, Remedy
from pipe.core.render.doctor.login import Connection, NoLogin
from pipe.core.render.doctor.policy import Action, Plan
from pipe.core.render.network import background, credentials, icons, theme, widgets
from pipe.core.render.network.attempts_panel import TriesPanel, TryRow

_REFRESH_MS = 60_000
_DATA_ROLE = QtCore.Qt.UserRole
_RED, _AMBER, _GREEN, _GREY = "#f08080", "#e0b060", "#8fe0a0", "#a3a6ad"
_ACTION_COLORS = {Action.HOLD: _RED, Action.WAIT: _GREY, Action.RETRY: _GREEN}
# Automatic retries that haven't checked in this long have likely stopped.
_STALE_SECONDS = 15 * 60
_RUN_COMMAND = "python -m pipe.core.render.doctor schedule --act --steer"
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
_OS_COLUMN, _FAILED_COLUMN = 3, 8
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
_AVOIDED = re.compile(r"!'([^']+)'")
_MEMORY = re.compile(r"@\.mem > (\d+)")


class DoctorPage(QtWidgets.QWidget):
    """`open_job` carries a shot's code and a job id, to show on its Farm tab."""

    open_job = QtCore.Signal(str, int)

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
    _steer: QtWidgets.QCheckBox
    _troubled: QtWidgets.QCheckBox
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
    _steer_chosen: bool

    def __init__(
        self, shot_codes: Sequence[str], parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._shot_codes = list(shot_codes)
        self._user = getpass.getuser()
        self._paths = records.default_paths()
        # Reads the headless doctor's history, so blades' records outlive their tasks.
        self._doctor = Doctor(History(self._paths.history))
        self._checkup, self._busy, self._steer_chosen = None, False, False
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
            "Refresh", "refresh", "Read Tractor's logs again now", clicked=self.refresh
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
                "Artist",
                "Tasks",
                "Problem",
                "Next step",
                "Tries",
                "Machines",
            ]
        )
        self._problems.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._problems.itemSelectionChanged.connect(self._problem_chosen)
        self._problems.itemDoubleClicked.connect(lambda *_: self._open_job())
        self._detail = QtWidgets.QTextBrowser()
        self._detail.setPlaceholderText("Choose a problem to see why its tasks failed.")
        self._mine = QtWidgets.QCheckBox("Only mine")
        self._mine.toggled.connect(lambda _: self._fill_problems())
        self._steer = QtWidgets.QCheckBox("Avoid failing machines")
        self._steer.setToolTip(
            "Retries skip the machines a task already failed on and the machines "
            "flagged in red, and wait for a machine with enough free memory if it ran "
            "out. Starts the same as the automatic retries."
        )
        self._steer.toggled.connect(lambda _: self._fill_problems())
        # Only a click, not the doctor's setting being copied, counts as a choice.
        self._steer.clicked.connect(self._choose_steering)
        self._retry = widgets.button(
            "Retry Selected",
            "retry",
            "Retry the selected tasks now",
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
        bar.addWidget(self._steer)
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
                "Group",
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
        self._blades.currentItemChanged.connect(lambda *_: self._machine_chosen())
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
            "red failed tasks from several jobs, so retries avoid them."
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
        machine_bar = QtWidgets.QHBoxLayout()
        machine_bar.addWidget(self._troubled)
        machine_bar.addStretch(1)
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
    ) -> tuple[Checkup, dict[str, Any] | None, list[dict[str, Any]], set[str]]:
        """On a pool thread: the farm, the headless doctor's files, and which
        machines are running Windows."""
        checkup = self._doctor.check()
        return (
            checkup,
            records.read_status(self._paths),
            records.read_journal(self._paths),
            windows.running_windows([health.blade for health in checkup.blades]),
        )

    def _read(
        self,
        found: tuple[Checkup, dict[str, Any] | None, list[dict[str, Any]], set[str]],
    ) -> None:
        self._busy = False
        self._checkup, status, journal, self._windows = found
        self._show_doctor_status(status)
        self._follow_doctor_steering(status)
        self._fill_problems()
        self._fill_blades()
        self._fill_activity(journal)
        unreadable = len(self._checkup.problems)
        self._status.setText(
            f"updated {time.strftime('%H:%M:%S')}"
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
            avoiding = (
                ", on machines that haven't failed them"
                if status.get("steering")
                else ""
            )
            text = (
                f"Automatic retries are on: failed tasks that can work are retried"
                f"{avoiding}. Last checked at {_clock(status.get('checked'))}."
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

    def _follow_doctor_steering(self, status: dict[str, Any] | None) -> None:
        """Steer as the running doctor does, so the tab's plans and retries
        match its, until the artist chooses otherwise."""
        if self._steer_chosen or status is None or not _running(status):
            return
        self._steer.blockSignals(True)
        self._steer.setChecked(bool(status.get("steering")))
        self._steer.blockSignals(False)

    def _choose_steering(self) -> None:
        self._steer_chosen = True

    # ---- filling -------------------------------------------------------

    def _plans(self) -> list[Plan]:
        if self._checkup is None:
            return []
        plans = self._checkup.plans(self._steer.isChecked())
        if self._mine.isChecked():
            plans = [p for p in plans if p.diagnosis.job.owner == self._user]
        return plans

    def _fill_problems(self) -> None:
        chosen = {_problem_key(p) for p in self._selected_problems()}
        self._problems.blockSignals(True)
        self._problems.clear()
        problems = group(self._plans())
        for problem in problems:
            first = problem.first
            d = first.diagnosis
            when = (
                f" at {_clock(first.retry_at)}" if first.action is Action.WAIT else ""
            )
            item = QtWidgets.QTreeWidgetItem(
                [
                    f"{problem.job.jid}  {problem.job.title}",
                    farm.shot_of(problem.job, self._shot_codes) or "—",
                    problem.job.owner,
                    str(len(problem.plans)),
                    _cause_label(d.latest.cause.key),
                    _ACTION_WORDS[first.action] + when,
                    str(max(p.diagnosis.attempts for p in problem.plans)),
                    ", ".join(d.same_way_blades[-4:]),
                ]
            )
            item.setData(0, _DATA_ROLE, problem)
            item.setForeground(
                5, QtGui.QBrush(QtGui.QColor(_ACTION_COLORS[first.action]))
            )
            item.setToolTip(4, _why(d))
            self._problems.addTopLevelItem(item)
            item.setSelected(_problem_key(problem) in chosen)
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
            blade = health.blade
            item = QtWidgets.QTreeWidgetItem(
                [
                    blade.name,
                    blade.profile,
                    _blade_state(health),
                    _system(health, self._windows),
                    f"{blade.free_memory_gb:.0f} GB",
                    f"{blade.free_disk_gb:.0f} GB",
                    str(blade.running or ""),
                    str(health.passed or ""),
                    str(len(health.failures) or ""),
                    "; ".join(_machine_notes(health)),
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
        self._machine_download.setEnabled(bool(health and health.failures))
        if item is None or health is None:
            self._machine_detail.clear()
            self._machine_tries.set_rows([])
            return
        self._machine_tries.set_rows(_machine_tries(health))
        lost = frozenset(f.key for f in health.failures if f.cause == _LOST)
        cached = self._lost_reports.get(health.blade.name)
        report = cached[1] if cached and cached[0] == lost else None
        self._machine_detail.setHtml(
            _describe_machine(health, report, health.blade.name in self._windows)
        )
        if lost and report is None:
            background.run(
                lambda: _read_lost(health),
                lambda found: self._lost_read(health, lost, found),
                lambda exc: self._status.setText(str(exc)),
            )

    def _lost_read(
        self, health: BladeHealth, lost: frozenset[str], report: _LostReport
    ) -> None:
        self._lost_reports[health.blade.name] = (lost, report)
        item = self._blades.currentItem()
        if item is not None and item.text(0) == health.blade.name:
            self._machine_detail.setHtml(
                _describe_machine(health, report, health.blade.name in self._windows)
            )

    def _fill_activity(self, journal: list[dict[str, Any]]) -> None:
        self._activity.clear()
        for entry in reversed(journal):
            services = entry.get("services") or []
            result = str(entry.get("result", ""))
            item = QtWidgets.QTreeWidgetItem(
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

    def _problem_chosen(self) -> None:
        problems = self._selected_problems()
        self._detail.setHtml(_describe(problems[0]) if problems else "")
        self._problem_tries.set_rows(_problem_tries(problems[0]) if problems else [])
        self._enable()

    # ---- acting --------------------------------------------------------

    def _retry_selected(self) -> None:
        plans = [
            plan for problem in self._selected_problems() for plan in problem.plans
        ]
        if not plans:
            return
        held = [p for p in plans if p.action is Action.HOLD]
        if held and not self._confirm_held(held):
            return
        settings = Settings(acting=True, steer=self._steer.isChecked())
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
            remembered = credentials.load()
            return remembered[1] if remembered and remembered[0] == self._user else None

        try:
            return login.connect(self._paths, self._user, keyring)
        except NoLogin as exc:
            raise NoLogin(
                "Retrying needs your Tractor login. Retry any task once on a shot's "
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

    def _open_job(self) -> None:
        problems = self._selected_problems()
        if not problems:
            return
        job = problems[0].job
        shot = farm.shot_of(job, self._shot_codes)
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
            for problem in self._selected_problems()
            for p in problem.plans
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
        for failure in health.failures:
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


def _blade_state(health: BladeHealth) -> str:
    blade = health.blade
    if _offline_for(health) is not None:
        return "Offline"
    if blade.nimby.startswith("/local/"):
        return "In Use (Nimby)"
    if blade.nimby:
        return "Off the farm (Nimby)"
    return "Rendering" if blade.running else "Available"


def _machine_notes(health: BladeHealth) -> list[str]:
    notes = []
    if (offline := _offline_for(health)) is not None:
        notes.append(f"Offline for {_ago(offline)}")
    if health.blade.free_disk_gb < LOW_DISK_GB:
        notes.append(f"Only {health.blade.free_disk_gb:.0f} GB of disk space free")
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
            text = farm.task_log(request.owner, request.jid, request.tid, request.title)
        except farm.FarmError as exc:
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
        raise farm.FarmError(f"Could not write {target} ({exc}).") from exc
    return saved, problems


def _describe_machine(
    health: BladeHealth, report: _LostReport | None, in_windows: bool
) -> str:
    """A machine's health and what failed on it, as HTML for the detail pane.
    `report` says what its lost tasks' logs show, None while they're read."""
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
        f"{html.escape(blade.profile)} · {blade.cpus} CPUs · "
        f"{html.escape(blade.gpu.split(',')[0] or 'no GPU')}<br>"
        f"{blade.free_memory_gb:.0f} GB memory and {blade.free_disk_gb:.0f} GB disk "
        f"free · rendering {blade.running} · last reported {reported}"
    )
    # The failures get their own section below.
    notes = "".join(
        f"<li>{html.escape(n)}</li>"
        for n in _machine_notes(health)
        if not n.startswith("Failed ")
    )
    head = (
        f"<h3>{html.escape(blade.name)} — {html.escape(_blade_state(health))}"
        f"{' · in Windows' if in_windows else ''}</h3>"
        f"{flagged}<p>{specs}</p>" + (f"<ul>{notes}</ul>" if notes else "")
    )
    if not health.failures:
        return head + (
            "<p>Nothing has failed here because of the machine in the last 12 hours.</p>"
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
                    ),
                )
            )
    return [row for _, row in sorted(rows, key=lambda r: r[0], reverse=True)]


def _machine_tries(health: BladeHealth) -> list[TryRow]:
    """The tries that failed on the machine because of it, latest first."""
    rows = []
    for f in reversed(health.failures):
        match = _ATTEMPT_KEY.match(f.key)
        if match is None:
            continue
        rows.append(
            TryRow(
                cells=(_clock(f.when), str(f.jid), f.task, _failure_label(f)),
                owner=f.owner,
                jid=int(match[1]),
                tid=int(match[2]),
                title=f.task,
                key=f.key,
                machine=health.blade.name,
            )
        )
    return rows


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
            found = attempts.parse(farm.task_log(owner, jid, tid))
        except farm.FarmError:
            continue
        lost += [_Lost(a.started, _doing(a.text)) for a in found if a.key in wanted]
    try:
        booted = farm.blade_booted(health.blade.name)
    except farm.FarmError:
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
    return result.capitalize()


def _kept_off(services: str) -> str:
    """What a retry's service keys asked for, in words."""
    machines = list(dict.fromkeys(_AVOIDED.findall(services)))
    memory = [int(m) for m in _MEMORY.findall(services)]
    parts = [f"Not on {_either(tuple(machines))}"] if machines else []
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


def _problem_key(problem: Problem) -> tuple[int, str]:
    return problem.job.jid, problem.first.diagnosis.latest.signature


def _clock(when: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(when)) if when else "?"


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _table(headers: list[str]) -> QtWidgets.QTreeWidget:
    table = QtWidgets.QTreeWidget()
    table.setHeaderLabels(headers)
    table.setRootIsDecorated(False)
    table.setUniformRowHeights(True)
    table.setAlternatingRowColors(True)
    return table
