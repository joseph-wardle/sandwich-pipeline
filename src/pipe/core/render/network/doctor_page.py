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
import time
from collections.abc import Sequence
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render import farm
from pipe.core.render.doctor import login, records
from pipe.core.render.doctor.blades import BladeHealth, History
from pipe.core.render.doctor.checkup import Checkup, Doctor, Problem, Settings, group
from pipe.core.render.doctor.login import Connection
from pipe.core.render.doctor.policy import Action, Plan
from pipe.core.render.network import background, credentials

_REFRESH_MS = 60_000
_DATA_ROLE = QtCore.Qt.UserRole
_RED, _AMBER, _GREEN, _GREY = "#f08080", "#e0b060", "#8fe0a0", "#a3a6ad"
_ACTION_COLORS = {Action.HOLD: _RED, Action.WAIT: _GREY, Action.RETRY: _GREEN}
# A headless doctor that hasn't checked in this long has likely stopped.
_STALE_SECONDS = 15 * 60
_RUN_COMMAND = "python -m pipe.core.render.doctor run --act"


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

    def __init__(
        self, shot_codes: Sequence[str], parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._shot_codes = list(shot_codes)
        self._user = getpass.getuser()
        self._paths = records.default_paths()
        # Reads the headless doctor's history, so blades' records outlive their tasks.
        self._doctor = Doctor(History(self._paths.history))
        self._checkup, self._busy = None, False

        title = QtWidgets.QLabel(
            "<b>Farm Doctor</b> — why the production's tasks fail, and what retrying can fix"
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
        tabs.addTab(self._build_blades(), "Blades")
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
                "Owner",
                "Tasks",
                "Cause",
                "What to do",
                "Attempts",
                "Blades",
            ]
        )
        self._problems.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._problems.itemSelectionChanged.connect(self._problem_chosen)
        self._problems.itemDoubleClicked.connect(lambda *_: self._open_job())
        self._detail = QtWidgets.QTextBrowser()
        self._detail.setPlaceholderText("Choose a problem to see why its tasks failed.")
        self._mine = QtWidgets.QCheckBox("Only mine")
        self._mine.toggled.connect(lambda _: self._fill_problems())
        self._steer = QtWidgets.QCheckBox("Steer retries off failing blades")
        self._steer.setToolTip(
            "Narrow each retried command's service key, so it keeps off the blades that "
            "failed it and, if it ran out of memory, waits for a blade with more free."
        )
        self._steer.toggled.connect(lambda _: self._fill_problems())
        self._retry = QtWidgets.QPushButton("Retry Selected")
        self._retry.setToolTip(
            "Retry the selected problems' tasks now, as the doctor plans to"
        )
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
                "Blade",
                "Profile",
                "State",
                "Free memory",
                "Free disk",
                "Running",
                "Passed",
                "Failures",
                "Problems",
                "GPU",
            ]
        )
        self._troubled = QtWidgets.QCheckBox("Only blades with problems")
        self._troubled.setChecked(True)
        self._troubled.toggled.connect(lambda _: self._fill_blades())
        hint = QtWidgets.QLabel(
            "Failures are the last 12 hours' failed attempts that were the blade's fault, "
            "such as running out of memory or losing a command. Suspect blades, in red, "
            "failed several jobs' work."
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
            ["When", "By", "Result", "Job", "Task", "Owner", "Cause", "Steered to"]
        )
        hint = QtWidgets.QLabel(
            f"From the doctor's journal, {self._paths.journal}, newest first."
        )
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
        if status is None:
            text = (
                "The headless doctor hasn't run on your account, so nothing retries by "
                f"itself. Start it with <code>{_RUN_COMMAND}</code>."
            )
        elif status.get("stopped"):
            text = f"The headless doctor stopped at {_clock(status['stopped'])} on {status.get('host')}."
        elif time.time() - float(status.get("checked") or 0) > _STALE_SECONDS:
            text = (
                f"The headless doctor on {status.get('host')} last checked at "
                f"{_clock(status.get('checked'))}, so it may have stopped."
            )
        else:
            doing = "retrying by itself" if status.get("acting") else "watching only"
            steering = ", steering retries" if status.get("steering") else ""
            text = (
                f"The headless doctor is {doing}{steering} on {status.get('host')}, last "
                f"checked at {_clock(status.get('checked'))}; {status.get('login')}."
            )
            if status.get("error"):
                text += f" Its last round failed: {status['error']}"
        self._doctor_status.setText(text)

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
                    d.latest.cause.label,
                    first.action.value + when,
                    str(max(p.diagnosis.attempts for p in problem.plans)),
                    ", ".join(d.same_way_blades[-4:]),
                ]
            )
            item.setData(0, _DATA_ROLE, problem)
            item.setForeground(
                5, QtGui.QBrush(QtGui.QColor(_ACTION_COLORS[first.action]))
            )
            item.setToolTip(4, d.why)
            self._problems.addTopLevelItem(item)
            item.setSelected(_problem_key(problem) in chosen)
        self._problems.blockSignals(False)
        for column in range(self._problems.columnCount()):
            self._problems.resizeColumnToContents(column)
        held = sum(len(p.plans) for p in problems if p.first.action is Action.HOLD)
        retrying = sum(len(p.plans) for p in problems) - held
        self._summary.setText(
            f"<span style='color:{_RED}'>{held} need a person</span> · "
            f"<span style='color:{_GREEN}'>{retrying} can be retried</span>"
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
                    "; ".join(health.problems),
                    blade.gpu.split(",")[0],
                ]
            )
            if health.suspect:
                for column in range(item.columnCount()):
                    item.setForeground(column, QtGui.QBrush(QtGui.QColor(_RED)))
                item.setToolTip(0, "Suspect: it failed several jobs' work")
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
                    str(entry.get("by", "doctor")),
                    result,
                    f"{entry.get('jid')}  {entry.get('job', '')}",
                    str(entry.get("task", "")),
                    str(entry.get("owner", "")),
                    str(entry.get("cause", "")),
                    "; ".join(str(s.get("after")) for s in services),
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
            item.setToolTip(2, str(entry.get("reason", "")))
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

        return login.connect(self._paths, self._user, keyring)

    def _retried(self, entries: list[dict[str, Any]]) -> None:
        retried = sum(1 for e in entries if e.get("result") == "retried")
        failed = [e for e in entries if e.get("result") != "retried"]
        text = f"Retried {_count(retried, 'task')}."
        if failed:
            text += f" {len(failed)} not: {failed[0].get('result')}"
        self._status.setText(text)
        self._enable()
        QtCore.QTimer.singleShot(3000, self.refresh)

    def _confirm_held(self, held: list[Plan]) -> bool:
        reasons = sorted({p.reason for p in held})
        answer = QtWidgets.QMessageBox.question(
            self,
            "Retry Anyway",
            f"The doctor thinks retrying won't help {len(held)} of these tasks:\n\n"
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
    color = _ACTION_COLORS[first.action]
    # The verdict's reason, unless it only repeats the cause's advice.
    why = "" if d.why.startswith(latest.cause.label) else f"<p>{html.escape(d.why)}</p>"
    plan = "" if first.reason == d.why else f" {html.escape(first.reason)}"
    rows = "".join(
        f"<tr><td>{f.attempt.number or ''}</td><td>{html.escape(f.attempt.blade)}</td>"
        f"<td>{html.escape(f.cause.label)}</td><td>{_clock(f.attempt.ended or f.attempt.started)}</td></tr>"
        for f in reversed(d.findings[-12:])
    )
    tasks = ", ".join(html.escape(p.diagnosis.task.title) for p in problem.plans[:30])
    if len(problem.plans) > 30:
        tasks += f" and {len(problem.plans) - 30} more"
    return (
        f"<h3>{problem.job.jid} {html.escape(problem.job.title)}</h3>"
        f"<p><b>{html.escape(latest.cause.label)}.</b> {html.escape(latest.cause.advice)}</p>"
        f"{why}<p><b style='color:{color}'>{first.action.value}.</b>{plan}</p>"
        f"<p><b>From the log</b> of {html.escape(d.task.title)}, attempt {latest.attempt.number} "
        f"on {html.escape(latest.attempt.blade)}:</p>"
        f"<pre style='white-space: pre-wrap'>{html.escape(latest.evidence)}</pre>"
        f"<p><b>Failed this way on:</b> {html.escape(', '.join(d.same_way_blades))}</p>"
        f"<p><b>Its failed attempts</b> ({len(d.findings)} of {d.attempts}), latest first:</p>"
        f"<table cellspacing='6'><tr><th>#</th><th>Blade</th><th>Cause</th><th>Ended</th></tr>{rows}</table>"
        f"<p><b>{_count(len(problem.plans), 'task')}:</b> {tasks}</p>"
    )


def _blade_state(health: BladeHealth) -> str:
    blade = health.blade
    if any(p.startswith("Not reporting") for p in health.problems):
        return "Not reporting"
    if blade.nimby.startswith("/local/"):
        return "In use (its user's jobs only)"
    if blade.nimby:
        return "Nimby"
    return blade.note.capitalize() if blade.note else "Up"


def _failure_list(health: BladeHealth) -> str:
    lines = [
        f"{_clock(f.when)}  J{f.jid} {f.task}: {f.label}"
        for f in reversed(health.failures[-15:])
    ]
    return "\n".join(lines)


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
