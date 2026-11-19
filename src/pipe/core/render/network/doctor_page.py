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
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render import farm
from pipe.core.render.doctor import login, records
from pipe.core.render.doctor import diagnose
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
from pipe.core.render.network import background, credentials

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
_AVOIDED = re.compile(r"!'([^']+)'")
_MEMORY = re.compile(r"@\.mem > (\d+)")


class DoctorPage(QtWidgets.QWidget):
    """`open_job` carries a shot's code and a job id, to show on its Farm tab."""

    open_job = QtCore.Signal(str, int)

    _problems: QtWidgets.QTreeWidget
    _detail: QtWidgets.QTextBrowser
    _blades: QtWidgets.QTreeWidget
    _activity: QtWidgets.QTreeWidget
    _summary: QtWidgets.QLabel
    _doctor_status: QtWidgets.QLabel
    _status: QtWidgets.QLabel
    _mine: QtWidgets.QCheckBox
    _steer: QtWidgets.QCheckBox
    _troubled: QtWidgets.QCheckBox
    _retry: QtWidgets.QPushButton
    _open: QtWidgets.QPushButton
    _timer: QtCore.QTimer
    _checkup: Checkup | None
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

        title = QtWidgets.QLabel(
            "<b>Farm Doctor</b> — why tasks failed, and which ones a retry will fix"
        )
        self._summary = QtWidgets.QLabel()
        self._summary.setStyleSheet("font-size: 13pt;")
        self._doctor_status = QtWidgets.QLabel()
        self._doctor_status.setWordWrap(True)
        self._doctor_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._status = QtWidgets.QLabel()
        self._status.setEnabled(False)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._summary)
        status_bar = QtWidgets.QHBoxLayout()
        status_bar.addWidget(self._doctor_status, 1)
        status_bar.addWidget(self._status)
        status_bar.addWidget(refresh)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_problems(), "Problems")
        tabs.addTab(self._build_blades(), "Machines")
        tabs.addTab(self._build_activity(), "Activity")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
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
        self._retry = QtWidgets.QPushButton("Retry Selected")
        self._retry.setToolTip("Retry the selected tasks now")
        self._retry.clicked.connect(self._retry_selected)
        self._open = QtWidgets.QPushButton("Open on Farm Tab")
        self._open.clicked.connect(self._open_job)

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(self._mine)
        bar.addWidget(self._steer)
        bar.addStretch(1)
        bar.addWidget(self._open)
        bar.addWidget(self._retry)
        split = QtWidgets.QSplitter()
        split.addWidget(self._problems)
        split.addWidget(self._detail)
        split.setSizes([720, 520])
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
        self._troubled.setChecked(True)
        self._troubled.toggled.connect(lambda _: self._fill_blades())
        hint = QtWidgets.QLabel(
            "Failed counts tasks from the last 12 hours that failed because of the "
            "machine itself, such as running out of memory or crashing. Machines in "
            "red failed tasks from several jobs, so retries avoid them."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(self._troubled)
        layout.addWidget(self._blades, 1)
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

    def _read_all(self) -> tuple[Checkup, dict[str, Any] | None, list[dict[str, Any]]]:
        """On a pool thread: the farm, and the headless doctor's files."""
        return (
            self._doctor.check(),
            records.read_status(self._paths),
            records.read_journal(self._paths),
        )

    def _read(
        self, found: tuple[Checkup, dict[str, Any] | None, list[dict[str, Any]]]
    ) -> None:
        self._busy = False
        self._checkup, status, journal = found
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
        self._blades.clear()
        if self._checkup is None:
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
                item.setToolTip(
                    0, "Flagged: it failed tasks from several jobs, so retries avoid it"
                )
            if health.failures:
                item.setToolTip(7, _failure_list(health))
            self._blades.addTopLevelItem(item)
        for column in range(self._blades.columnCount()):
            self._blades.resizeColumnToContents(column)

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

    def _selected_problems(self) -> list[Problem]:
        return [item.data(0, _DATA_ROLE) for item in self._problems.selectedItems()]

    def _enable(self) -> None:
        chosen = bool(self._problems.selectedItems())
        self._retry.setEnabled(chosen)
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
    rows = "".join(
        f"<tr><td>{f.attempt.number or ''}</td><td>{html.escape(f.attempt.blade)}</td>"
        f"<td>{html.escape(_cause_label(f.cause.key))}</td>"
        f"<td>{_clock(f.attempt.ended or f.attempt.started)}</td></tr>"
        for f in reversed(d.findings[-12:])
    )
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
        f"<p><b>Failed tries</b> ({len(d.findings)} of {d.attempts}), latest first:</p>"
        "<table cellspacing='6'><tr><th>#</th><th>Machine</th><th>Problem</th>"
        f"<th>Ended</th></tr>{rows}</table>"
        f"<p><b>{_count(len(problem.plans), 'task')}:</b> {tasks}</p>"
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
