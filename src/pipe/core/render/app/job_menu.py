"""The right-click menu of a table of Tractor jobs: Pause, Change Priority, Archive.

Shared by the Tractor Overview, a shot's Farm tab and Farm Doctor's Problems.
Who may do these is Tractor's to say, as it always was for archiving: each
asks for the window's one Tractor login, and the engine refuses it unless it
is the job's owner or a Tractor administrator.
"""

from __future__ import annotations

from collections.abc import Callable

from Qt import QtCore, QtWidgets

from pipe.core import tractor
from pipe.core.render.app import background, icons
from pipe.core.render.app.login import TractorLogin
from pipe.core.tractor import Job, LoginRefused

# Listed in a confirmation, at most; the rest are counted.
_LISTED = 12
# Tractor takes any number; these keep a mistyped one sensible.
_PRIORITY_RANGE = (0.0, 100_000.0)


class JobMenu(QtCore.QObject):
    """Acts on the jobs a table's right-click chose.

    `message` carries a line for the page's status label, and `changed` the
    note to show once the page has read Tractor again, which it should do now.
    """

    message = QtCore.Signal(str)
    changed = QtCore.Signal(str)

    _parent: QtWidgets.QWidget
    _login: TractorLogin

    def __init__(self, parent: QtWidgets.QWidget, login: TractorLogin) -> None:
        super().__init__(parent)
        self._parent, self._login = parent, login

    def attach(
        self,
        table: QtWidgets.QTreeWidget,
        jobs_of: Callable[[list[QtWidgets.QTreeWidgetItem]], list[Job]],
    ) -> None:
        """Open the menu on `table`'s right-click, for the jobs `jobs_of` finds
        in its selected rows. A right-click on an unselected row chooses it alone."""

        def show(position: QtCore.QPoint) -> None:
            item = table.itemAt(position)
            if item is None:
                return
            if not item.isSelected():
                table.setCurrentItem(item)
            jobs = _unique(jobs_of(table.selectedItems()))
            if jobs:
                self.exec_(jobs, table.viewport().mapToGlobal(position))

        table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(show)

    def exec_(self, jobs: list[Job], at: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self._parent)
        menu.setToolTipsVisible(True)
        self.add_actions(menu, jobs)
        menu.exec_(at)

    def add_actions(self, menu: QtWidgets.QMenu, jobs: list[Job]) -> None:
        paused = [job for job in jobs if job.paused]
        running = [job for job in jobs if not job.paused]
        if running:
            action = menu.addAction(icons.get("pause"), _label("Pause", running, jobs))
            action.setToolTip(
                "Start no new tasks of these jobs. Tasks rendering now finish."
            )
            action.triggered.connect(lambda: self._pause(running, True))
        if paused:
            action = menu.addAction(icons.get("play"), _label("Resume", paused, jobs))
            action.setToolTip("Let Tractor start these jobs' tasks again.")
            action.triggered.connect(lambda: self._pause(paused, False))
        action = menu.addAction(icons.get("chart"), "Change Priority…")
        action.setToolTip("Higher priority jobs are started first.")
        action.triggered.connect(lambda: self._change_priority(jobs))
        menu.addSeparator()
        action = menu.addAction(
            icons.get("archive"), _label("Archive", jobs, jobs) + "…"
        )
        action.setToolTip(
            "Move these jobs to Tractor's archive. Their frames stay on disk."
        )
        action.triggered.connect(lambda: self._archive(jobs))

    # ---- actions -------------------------------------------------------

    def _pause(self, jobs: list[Job], paused: bool) -> None:
        doing = "Pausing" if paused else "Resuming"
        done = "Paused" if paused else "Resumed"
        self._run(
            jobs,
            doing,
            lambda user, password: tractor.pause(jobs, user, password, paused),
            lambda jids: f"{done} {_jobs_listed(jids)}",
        )

    def _change_priority(self, jobs: list[Job]) -> None:
        low, high = _PRIORITY_RANGE
        priority, ok = QtWidgets.QInputDialog.getDouble(
            self._parent,
            "Change Priority",
            f"New priority for {_count(len(jobs), 'job')} "
            f"(now {_priorities(jobs)}). Higher is started sooner.",
            max(min(jobs[0].priority, high), low),
            low,
            high,
            1,
        )
        if not ok:
            return
        self._run(
            jobs,
            "Changing the priority of",
            lambda user, password: tractor.set_priority(jobs, priority, user, password),
            lambda jids: f"Set priority {priority:g} on {_jobs_listed(jids)}",
        )

    def _archive(self, jobs: list[Job]) -> None:
        if not self._confirm_archive(jobs):
            return
        self._run(
            jobs,
            "Archiving",
            lambda user, password: tractor.archive(jobs, user, password),
            lambda jids: f"Archived {_jobs_listed(jids)}",
        )

    def _confirm_archive(self, jobs: list[Job]) -> bool:
        listed = "\n".join(
            f"  {job.jid}  {job.title}  ({job.owner})" for job in jobs[:_LISTED]
        )
        if len(jobs) > _LISTED:
            listed += f"\n  …and {len(jobs) - _LISTED} more"
        unfinished = [job for job in jobs if job.done < job.total]
        warning = (
            f"\n\n{_count(len(unfinished), 'of them is', 'of them are')} not finished: "
            "archiving stops it, and Tractor kills any frames it is rendering."
            if unfinished
            else ""
        )
        answer = QtWidgets.QMessageBox.question(
            self._parent,
            "Archive Jobs",
            f"Archive {_count(len(jobs), 'job')} from Tractor?\n\n{listed}{warning}\n\n"
            "Archived jobs leave this list. Their frames stay on disk, and Tractor's "
            "dashboard can restore them from its archive.",
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _run(
        self,
        jobs: list[Job],
        doing: str,
        act: Callable[[str, str], list[int]],
        said: Callable[[list[int]], str],
    ) -> None:
        owners = sorted({job.owner for job in jobs})
        login = self._login.current(self._parent, owners, doing)
        if login is None:
            return
        user, password = login
        self.message.emit(f"{doing} {_count(len(jobs), 'job')}…")
        background.run(
            lambda: act(user, password),
            lambda jids: self.changed.emit(said(jids)),
            self._failed,
        )

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, LoginRefused):
            self._login.refused()
        self.message.emit(str(exc))
        # What did happen before it stopped shows once the page reads Tractor.
        QtCore.QTimer.singleShot(1000, lambda: self.changed.emit(""))


def _unique(jobs: list[Job]) -> list[Job]:
    """Each job once, in order: a job's row and rows beneath it may both be chosen."""
    seen: dict[int, Job] = {}
    for job in jobs:
        seen.setdefault(job.jid, job)
    return list(seen.values())


def _label(verb: str, these: list[Job], chosen: list[Job]) -> str:
    """'Pause', or 'Pause 2 of 3 Jobs' when only some of those chosen qualify."""
    if len(chosen) == 1:
        return f"{verb} Job"
    if len(these) == len(chosen):
        return f"{verb} {len(these)} Jobs"
    return f"{verb} {len(these)} of {len(chosen)} Jobs"


def _priorities(jobs: list[Job]) -> str:
    found = sorted({job.priority for job in jobs})
    if len(found) == 1:
        return f"{found[0]:g}"
    return f"{found[0]:g} to {found[-1]:g}"


def _jobs_listed(jids: list[int]) -> str:
    return f"{_count(len(jids), 'job')}: {', '.join(map(str, jids))}"


def _count(n: int, one: str, many: str | None = None) -> str:
    """'1 job', '3 jobs'; or with `many`, '1 of them is', '2 of them are'."""
    return f"{n} {one if n == 1 else many or one + 's'}"
