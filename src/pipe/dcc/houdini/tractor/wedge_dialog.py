"""The Wedge Send dialog: the parms to wedge and their values, the frames, what
the flipbook draws, whether it also renders, what each wedge rebuilds, and how
the job is named and run."""

from __future__ import annotations

import math
import shlex
from collections.abc import Callable
from dataclasses import dataclass

import hou
from Qt import QtCore, QtGui, QtWidgets

from pipe.core.ui.dialogs import DialogButtons
from pipe.dcc.houdini.tractor import SendRefused, wedges
from pipe.dcc.houdini.tractor.job import BLADES, PRIORITY

# A parm path and the values typed for it.
Row = tuple[str, str]
# The caches each wedge writes, given the wedged parms and the Draw.
Rebuilds = Callable[[list[hou.Parm], str], list[hou.Node]]
# The job's default title, given the node.
Titled = Callable[[hou.Node], str]

# One wedge is just a farm sim.
MINIMUM = 2
# The Draw menu.
DRAWS = {"Shot": wedges.SHOT, "Cache only": wedges.CACHE}
DRAW_HELP = (
    "Shot draws the stage at the publish node, through the shot camera.\n"
    "Cache only draws just this cache, through the same camera, and renders\n"
    "it under the shot's lights."
)
FLIPBOOK = "Flipbook"
# Each wedge also renders in RenderMan at daily quality, denoised.
RENDER = "Flipbook + Render"
OUTPUT_HELP = (
    "Render also renders each wedge in RenderMan with the shot's lights and\n"
    "materials, at daily quality, and denoises it. It costs farm time per wedge."
)
# The resolution menu, by height, as artists name them.
RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080)}
REBUILDS_HELP = (
    "The caches each wedge writes into its own folder: every cache loading\n"
    "from disk between a wedged parm and what the flipbook draws, and this\n"
    "cache when a wedged parm changes it.\n"
    "They are found from the references Houdini reports, which leave out\n"
    "parms read by VEX code and nodes named by a path built from an expression."
)
_WIDTH = 520
# A spin box allows 0 to 99 unless told otherwise; frames can be negative.
_FRAME_LIMIT = 100_000
# Houdini's own icon for removing a multiparm instance.
_REMOVE_ICON = "BUTTONS_multi_remove"


@dataclass(frozen=True)
class WedgeChoice:
    title: str
    priority: int
    service: str
    start: int
    end: int
    draw: str
    # As typed, so the next dialog for this node starts from them.
    rows: list[Row]
    # The width and height each wedge also renders at, or None for flipbooks only.
    render: tuple[int, int] | None


# The last dialog of each node by its session id, raised when its button is
# pressed again. Unlike a path, the id survives a rename and names no node of
# the next hip opened.
_dialogs: dict[int, _WedgeDialog] = {}


def show(
    parent: QtWidgets.QWidget | None,
    node: hou.Node,
    titled: Titled,
    frames: tuple[int, int],
    draw: str,
    rows: list[Row],
    rebuilds: Rebuilds,
    send: Callable[[WedgeChoice], None],
) -> None:
    """Open the dialog, which calls `send` on Send and stays open while it
    raises SendRefused."""
    # The stubs say sessionId() returns None. It returns the node's id.
    key: int = node.sessionId()  # ty: ignore[invalid-assignment]
    old = _dialogs.get(key)
    if old is not None and old.isVisible():
        old.raise_()
        old.activateWindow()
        return
    if old is not None:
        old.deleteLater()
    dialog = _WedgeDialog(parent, node, titled, frames, draw, rows, rebuilds, send)
    _dialogs[key] = dialog
    dialog.show()


def count(rows: list[Row]) -> int:
    """How many wedges the rows make, before their values are checked."""
    sizes = []
    for path, values in rows:
        if not path.strip():
            continue
        try:
            sizes.append(len(shlex.split(values)))
        except ValueError:
            # An unclosed quote, which Send explains.
            sizes.append(0)
    return math.prod(sizes) if sizes else 0


class _ParmField(QtWidgets.QLineEdit):
    """Takes a parm dragged from the parameter pane."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setPlaceholderText("Drag a parm here")

    # Qt's stubs name the event `arg__1`, hence the override ignores. hou.qt
    # is Houdini's own Python, which the hou stubs leave out.
    def dragEnterEvent(
        self, event: QtGui.QDragEnterEvent
    ) -> None:  # ty: ignore[invalid-method-override]
        kind = hou.qt.mimeType.parmPath  # ty: ignore[unresolved-attribute]
        if event.mimeData().hasFormat(kind):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(
        self, event: QtGui.QDropEvent
    ) -> None:  # ty: ignore[invalid-method-override]
        kind = hou.qt.mimeType.parmPath  # ty: ignore[unresolved-attribute]
        data = event.mimeData().data(kind)
        if data.isEmpty():
            super().dropEvent(event)
            return
        # Several dragged parms come separated by tabs; a row holds one.
        self.setText(bytes(data.data()).decode().split("\t")[0])
        event.acceptProposedAction()


def _choose(field: QtWidgets.QLineEdit, chosen: list[hou.Parm | None]) -> None:
    # Houdini's chooser sends a list, empty when it is cancelled.
    if chosen and chosen[0] is not None:
        field.setText(chosen[0].path())


class _WedgeDialog(QtWidgets.QDialog, DialogButtons):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        node: hou.Node,
        titled: Titled,
        frames: tuple[int, int],
        draw: str,
        rows: list[Row],
        rebuilds: Rebuilds,
        send: Callable[[WedgeChoice], None],
    ) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Send", "Cancel")
        self.setMinimumWidth(_WIDTH)
        # The stubs say sessionId() returns None. It returns the node's id.
        self._key: int = node.sessionId()  # ty: ignore[invalid-assignment]
        self._titled = titled
        self._default_title = ""
        self._rebuilds = rebuilds
        self._send = send
        self._fields: list[tuple[_ParmField, QtWidgets.QLineEdit]] = []
        # Every widget of each row, in the order of `_fields`.
        self._rows: list[list[QtWidgets.QWidget]] = []

        self._title = QtWidgets.QLineEdit()

        low, high, default = PRIORITY
        self._priority = QtWidgets.QSpinBox()
        self._priority.setRange(low, high)
        self._priority.setValue(default)

        self._blades = QtWidgets.QComboBox()
        self._blades.addItems(list(BLADES))

        self._start = QtWidgets.QSpinBox()
        self._end = QtWidgets.QSpinBox()
        frame_row = QtWidgets.QHBoxLayout()
        for box, value in ((self._start, frames[0]), (self._end, frames[1])):
            box.setRange(-_FRAME_LIMIT, _FRAME_LIMIT)
            box.setValue(value)
            box.valueChanged.connect(self._update_count)
            frame_row.addWidget(box)
        frame_row.addStretch(1)

        self._draw = QtWidgets.QComboBox()
        self._draw.addItems(list(DRAWS))
        self._draw.setCurrentIndex(list(DRAWS.values()).index(draw))
        self._draw.setToolTip(DRAW_HELP)
        self._draw.currentTextChanged.connect(self._update_rebuilds)

        self._output = QtWidgets.QComboBox()
        self._output.addItems([FLIPBOOK, RENDER])
        self._output.setToolTip(OUTPUT_HELP)
        self._output.currentTextChanged.connect(self._update_output)
        self._resolution = QtWidgets.QComboBox()
        self._resolution.addItems(list(RESOLUTIONS))

        self._rebuilt = QtWidgets.QLabel()
        self._rebuilt.setWordWrap(True)
        self._rebuilt.setToolTip(REBUILDS_HELP)

        self._grid = QtWidgets.QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        for path, values in rows or [("", "")]:
            self._add_row(path, values)
        add = QtWidgets.QPushButton("Add Parm")
        add.clicked.connect(lambda: self._add_row("", ""))
        wedge_column = QtWidgets.QVBoxLayout()
        wedge_column.addLayout(self._grid)
        wedge_column.addWidget(add)
        self._count = QtWidgets.QLabel()

        self._form = form = QtWidgets.QFormLayout()
        form.addRow("Title", self._title)
        form.addRow("Priority", self._priority)
        form.addRow("Blades", self._blades)
        form.addRow("Frames", frame_row)
        form.addRow("Draw", self._draw)
        form.addRow("Output", self._output)
        form.addRow("Resolution", self._resolution)
        form.addRow("Wedge", wedge_column)
        form.addRow("", self._count)
        form.addRow("Rebuilds per wedge", self._rebuilt)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self._update_count()
        self._update_output()
        self._refresh()

    def _add_row(self, path: str, values: str) -> None:
        parm = _ParmField(path)
        chooser = hou.qt.ParmChooserButton()  # ty: ignore[unresolved-attribute]
        chooser.parmSelected.connect(lambda chosen: _choose(parm, chosen))
        value_field = QtWidgets.QLineEdit(values)
        value_field.setPlaceholderText("Values, like 0.5 1 2")
        for field in (parm, value_field):
            field.textChanged.connect(self._update_count)
        parm.textChanged.connect(self._update_rebuilds)
        remove = QtWidgets.QToolButton()
        remove.setIcon(hou.qt.Icon(_REMOVE_ICON))
        remove.setAutoRaise(True)
        remove.setToolTip("Remove this parm")
        widgets = [parm, chooser, value_field, remove]
        remove.clicked.connect(lambda: self._remove_row(widgets))
        row = self._grid.rowCount()
        for column, widget in enumerate(widgets):
            self._grid.addWidget(widget, row, column)
        self._fields.append((parm, value_field))
        self._rows.append(widgets)

    def _remove_row(self, widgets: list[QtWidgets.QWidget]) -> None:
        index = self._rows.index(widgets)
        if len(self._rows) == 1:
            # The last row is cleared, so there is always one to fill.
            for field in self._fields[0]:
                field.clear()
            return
        del self._rows[index]
        del self._fields[index]
        for widget in widgets:
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._update_count()
        self._update_rebuilds()

    def rows(self) -> list[Row]:
        return [
            (parm.text().strip(), values.text().strip())
            for parm, values in self._fields
            if parm.text().strip() or values.text().strip()
        ]

    def event(self, event: QtCore.QEvent) -> bool:
        # The artist comes back from editing the graph, or renaming the node.
        if event.type() == QtCore.QEvent.WindowActivate:
            self._refresh()
        return super().event(event)

    def _refresh(self) -> None:
        node = hou.nodeBySessionId(self._key)
        # A deleted node keeps its name here, and Rebuilds says it is gone.
        if node is not None:
            self.setWindowTitle(f"Wedge {node.name()} on Tractor")
            self._default_title = self._titled(node)
            self._title.setPlaceholderText(self._default_title)
        self._update_rebuilds()

    def _update_rebuilds(self) -> None:
        # A path still being typed names no parm yet.
        parms = [hou.parm(path) for path, _ in self.rows()]
        draw = DRAWS[self._draw.currentText()]
        try:
            rebuilt = self._rebuilds([p for p in parms if p is not None], draw)
            text = ", ".join(c.name() for c in rebuilt) or "Nothing"
        except SendRefused as refusal:
            text = str(refusal)
        self._rebuilt.setText(text)
        # A shown dialog doesn't grow for a label that wraps onto more lines.
        # Before it is shown, a resize would stop it sizing itself to fit.
        label = self._rebuilt
        grow = label.heightForWidth(label.width()) - label.height()
        if self.isVisible() and grow > 0:
            self.resize(self.width(), self.height() + grow)

    def _update_output(self) -> None:
        rendered = self._output.currentText() == RENDER
        self._resolution.setVisible(rendered)
        self._form.labelForField(self._resolution).setVisible(rendered)

    def _update_count(self) -> None:
        total = count(self.rows())
        self._count.setText(f"{total} wedge" if total == 1 else f"{total} wedges")
        send = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        send.setEnabled(total >= MINIMUM and self._start.value() <= self._end.value())

    def accept(self) -> None:
        try:
            self._send(self.choice())
        except SendRefused as refusal:
            # The dialog stays open, so the rows aren't typed again.
            QtWidgets.QMessageBox.warning(self, self.windowTitle(), str(refusal))
            return
        super().accept()

    def choice(self) -> WedgeChoice:
        return WedgeChoice(
            title=self._title.text().strip() or self._default_title,
            priority=self._priority.value(),
            service=BLADES[self._blades.currentText()],
            start=self._start.value(),
            end=self._end.value(),
            draw=DRAWS[self._draw.currentText()],
            rows=self.rows(),
            render=(
                RESOLUTIONS[self._resolution.currentText()]
                if self._output.currentText() == RENDER
                else None
            ),
        )
