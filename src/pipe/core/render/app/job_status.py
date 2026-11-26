"""A Tractor job's status in a word or two, coloured alike on every page that lists jobs."""

from __future__ import annotations

from Qt import QtGui, QtWidgets

from pipe.core.tractor import ERROR, PAUSED, RENDERING, WAITING, Job

DONE = "Done"
COLORS = {
    ERROR: "#e06c6c",
    PAUSED: "#c9a14a",
    RENDERING: "#6ca0e0",
    WAITING: "#c9c9c9",
    DONE: "#7d8088",
}
_TIPS = {
    ERROR: "Some of its tasks failed",
    PAUSED: "Paused: Tractor starts none of its tasks until it is resumed",
    RENDERING: "Rendering now",
    WAITING: "Waiting for a free machine",
    DONE: "Every task finished",
}


def words(job: Job) -> list[str]:
    """Its errors, if any, then where it is: 'Error', 'Rendering' for a job
    that failed some tasks and renders others."""
    found = [ERROR] if job.errors else []
    if job.paused:
        found.append(PAUSED)
    elif job.active:
        found.append(RENDERING)
    elif job.finished:
        found.append(DONE)
    else:
        found.append(WAITING)
    return found


def text(job: Job) -> str:
    return " · ".join(words(job))


def show(item: QtWidgets.QTreeWidgetItem, column: int, job: Job) -> None:
    """Write the job's status into `item`'s `column`, coloured by its first word."""
    found = words(job)
    item.setText(column, " · ".join(found))
    item.setForeground(column, QtGui.QBrush(QtGui.QColor(COLORS[found[0]])))
    item.setToolTip(column, ". ".join(_TIPS[word] for word in found) + ".")
