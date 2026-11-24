"""The Render Network's parms as Qt widgets, showing what its Houdini session reports."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.app.marks import ChangeDot, dot_icon
from pipe.core.render.app.scene_graph import (
    PrimPicker,
    TypeFilter,
    is_camera,
    is_light,
)
from pipe.core.render.protocol import EXPRESSION, LANGUAGE, ParmState, Prim

Entry = dict[str, Any]

# Houdini's parm template and folder type names.
FOLDER = "Folder"
TOGGLE = "Toggle"
MENU = "Menu"
STRING = "String"
INT = "Int"
FLOAT = "Float"
LABEL = "Label"
SEPARATOR = "Separator"
TABS = ("Tabs", "RadioButtons")
COLLAPSIBLE = "Collapsible"
MULTIPARMS = ("MultiparmBlock", "ScrollingMultiparmBlock", "TabbedMultiparmBlock")
HSCRIPT = "Hscript"
# Multiparm children name their instance with this, e.g. `lpename_#`.
INSTANCE_TOKEN = "#"
# Buttons are left out: the window has its own Send.
_DRAWN = (TOGGLE, MENU, STRING, INT, FLOAT, LABEL, SEPARATOR)

LABEL_WIDTH = 170
NUMBER_WIDTH = 90
_BLANK_LOOK = "blank"
_EXPRESSION_STYLE = "QLineEdit { background-color: #2d4a32; }"
_NOTE_STYLE = "QLabel { background-color: palette(alternate-base); border-radius: 4px; padding: 8px; }"

# Parms the asset doesn't tag as prim paths, but which name prims of one kind:
# (picker filter, whether only one prim makes sense).
_TYPED_PICKERS: dict[str, tuple[TypeFilter, bool]] = {
    "camOverride": (is_camera, True),
    "lightlayer_lights": (is_light, False),
}

Show = Callable[[ParmState], None]
SetFlag = Callable[[bool], None]


class ParmForm(QtWidgets.QWidget):
    """The parms of `schema`. `edited` and `instance_removed` ask Houdini for a change."""

    edited = QtCore.Signal(str, object)
    instance_removed = QtCore.Signal(str, int)

    _schema: list[Entry]
    _parms: dict[str, ParmState]
    _prims: Sequence[Prim]
    # Folder and tab-bar states by name, kept across rebuilds and reloads.
    _state: dict[str, Any]
    _saved: dict[str, list[Any]]
    _shows: list[tuple[str, Show]]
    _flags: list[tuple[str, SetFlag, SetFlag]]
    _body: QtWidgets.QWidget | None
    # Each dot's parm names, and how to show whether any of them changed.
    _marks: list[tuple[set[str], SetFlag]]
    # The names of the folders and tabs being built, innermost last.
    _scopes: list[set[str]]

    def __init__(
        self,
        schema: list[Entry],
        parms: dict[str, ParmState],
        prims: Sequence[Prim],
        state: dict[str, Any],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._schema, self._parms, self._prims, self._state = (
            schema,
            parms,
            prims,
            state,
        )
        self._saved = _values(parms)
        self._body = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._build()

    def show_parms(self, parms: dict[str, ParmState]) -> None:
        names_changed = parms.keys() != self._parms.keys()
        self._parms = parms
        # A multiparm gained or lost instances.
        if names_changed:
            self._build()
            return
        for name, show in self._shows:
            show(parms[name])
        self._refresh()

    def mark_saved(self) -> None:
        self._saved = _values(self._parms)
        self._refresh()

    def set_prims(self, prims: Sequence[Prim]) -> None:
        self._prims = prims

    def evaluated(self, name: str) -> list[Any]:
        state = self._parms.get(name)
        return state.evaluated if state else []

    # ---- building ------------------------------------------------------

    def _build(self) -> None:
        if self._body is not None:
            self._body.hide()
            self._body.deleteLater()
        self._shows, self._flags, self._marks, self._scopes = [], [], [], []
        self._body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self._body)
        layout.setContentsMargins(8, 8, 8, 8)
        self._add_entries(self._schema, layout, "")
        layout.addStretch(1)
        self.layout().addWidget(self._body)
        self._refresh()

    def _add_entries(
        self, entries: list[Entry], layout: QtWidgets.QVBoxLayout, instance: str
    ) -> None:
        # A folder's header toggle is drawn in its header, not on its own row.
        headers = {e["header_toggle"] for e in entries if e.get("header_toggle")}
        index = 0
        while index < len(entries):
            entry = entries[index]
            if _is_tabs(entry):
                run = _run(entries, index, _is_tabs)
                layout.addWidget(self._tabs(run, instance))
                index += len(run)
            elif entry["type"] != FOLDER:
                row = _joined(entries, index)
                index += len(row)
                row = [
                    e for e in row if e["name"] not in headers and e["type"] in _DRAWN
                ]
                if row:
                    layout.addWidget(self._row(row, instance))
            elif entry["folder_type"] in MULTIPARMS:
                layout.addWidget(self._multiparm(entry, instance))
                index += 1
            else:
                layout.addWidget(self._folder(entry, instance))
                index += 1

    def _row(self, entries: list[Entry], instance: str) -> QtWidgets.QWidget:
        first = entries[0]
        first_name = _instanced(first["name"], instance)
        if first["type"] == SEPARATOR:
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Sunken)
            self._flag(first_name, line.setEnabled, line.setVisible)
            return line
        if first["type"] == LABEL and len(entries) == 1:
            note = QtWidgets.QLabel(first.get("text", ""))
            note.setWordWrap(True)
            note.setStyleSheet(_NOTE_STYLE)
            self._flag(first_name, note.setEnabled, note.setVisible)
            return note

        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 1, 0, 1)
        dot = ChangeDot()
        layout.addWidget(dot)
        self._mark(
            {
                _instanced(e["name"], instance)
                for e in entries
                if e["type"] not in (LABEL, SEPARATOR)
            },
            dot.set_changed,
        )
        title = QtWidgets.QLabel()
        title.setFixedWidth(LABEL_WIDTH)
        title.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(title)

        # A label-less toggle first, as for Override Camera, puts its checkbox
        # before the parm it switches, and that parm names the row.
        named = first
        if first["type"] == TOGGLE and first["no_label"] and len(entries) > 1:
            named = entries[1]
        if not (named["type"] == TOGGLE or named["no_label"]):
            title.setText(named["label"])
            title.setToolTip(_tooltip(named, instance))
        for entry in entries:
            name = _instanced(entry["name"], instance)
            if entry is not named and entry["type"] != TOGGLE and not entry["no_label"]:
                inline = QtWidgets.QLabel(entry["label"])
                layout.addWidget(inline)
                self._flag(name, inline.setEnabled, inline.setVisible)
            field = self._field(entry, name, _tooltip(entry, instance))
            self._flag(name, field.setEnabled, field.setVisible)
            layout.addWidget(field)
        layout.addStretch(1)
        self._flag(
            _instanced(named["name"], instance), title.setEnabled, row.setVisible
        )
        return row

    def _field(self, entry: Entry, name: str, tooltip: str) -> QtWidgets.QWidget:
        kind = entry["type"]
        if kind == TOGGLE:
            box = QtWidgets.QCheckBox("" if entry["no_label"] else entry["label"])
            box.setToolTip(tooltip)
            box.clicked.connect(lambda on: self.edited.emit(name, [on]))
            self._show(name, lambda state: box.setChecked(bool(state.evaluated[0])))
            return box
        if kind == MENU:
            return self._menu(entry, name, tooltip)
        if kind == LABEL:
            return QtWidgets.QLabel(entry.get("text", ""))
        return self._line_edits(entry, name, tooltip)

    def _menu(self, entry: Entry, name: str, tooltip: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setToolTip(tooltip)
        for token, label in entry["menu"]:
            combo.addItem(label, token)

        def show(state: ParmState) -> None:
            token = state.evaluated[0]
            if combo.findData(token) < 0:
                # A value the menu lacks, such as an expression's.
                combo.addItem(str(token), token)
            combo.setCurrentIndex(combo.findData(token))

        combo.activated.connect(
            lambda index: self.edited.emit(name, [combo.itemData(index)])
        )
        self._show(name, show)
        return combo

    def _line_edits(self, entry: Entry, name: str, tooltip: str) -> QtWidgets.QWidget:
        """A string or number field per component."""
        holder = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        is_text = entry["type"] == STRING
        picker = self._picker_for(entry) if is_text else None
        edits: list[QtWidgets.QLineEdit] = []
        for _ in range(entry["size"]):
            edit = _PathEdit() if picker is not None else QtWidgets.QLineEdit()
            if is_text:
                edit.setMinimumWidth(320)
            else:
                edit.setFixedWidth(NUMBER_WIDTH)
            edit.editingFinished.connect(lambda: self._commit(entry, name, edits))
            edits.append(edit)
            layout.addWidget(edit, 1 if is_text else 0)
        if picker is not None:
            only, single = picker
            button = QtWidgets.QToolButton()
            button.setText("…")
            button.setToolTip("Choose prims from the shot's stage")
            button.clicked.connect(lambda: self._pick(entry, name, edits, only, single))
            layout.addWidget(button)

        def show(state: ParmState) -> None:
            for edit, value, result in zip(edits, state.value, state.evaluated):
                # Not over what the artist is typing.
                if not edit.isModified():
                    edit.setText(_shown(value))
                _style(edit, value, result, tooltip)

        self._show(name, show)
        return holder

    def _folder(self, entry: Entry, instance: str) -> QtWidgets.QWidget:
        key = _instanced(entry["name"], instance)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        self._scopes.append(inside := set())
        self._add_entries(entry["children"], body_layout, instance)
        self._scopes.pop()

        if entry["folder_type"] != COLLAPSIBLE:
            if entry["look"] == _BLANK_LOOK:
                body_layout.setContentsMargins(0, 0, 0, 0)
                self._flag(key, body.setEnabled, body.setVisible)
                return body
            box = QtWidgets.QGroupBox(entry["label"])
            QtWidgets.QVBoxLayout(box).addWidget(body)
            self._flag(key, body.setEnabled, box.setVisible)
            return box

        toggle = _instanced(entry["header_toggle"], instance)
        on = toggle in self._parms and bool(self.evaluated(toggle)[0])
        section = _Section(entry["label"], self._state.get(key, on))
        section.expandedChanged.connect(
            lambda open_: self._state.__setitem__(key, open_)
        )
        if toggle:
            box = section.add_toggle()
            box.clicked.connect(lambda checked: self.edited.emit(toggle, [checked]))
            self._show(toggle, lambda state: box.setChecked(bool(state.evaluated[0])))
        section.set_body(body)
        # Its dot shows changes to parms hidden while it's closed.
        self._mark(inside | {toggle} - {""}, section.set_changed)
        self._flag(key, body.setEnabled, section.setVisible)
        return section

    def _tabs(self, entries: list[Entry], instance: str) -> QtWidgets.QTabWidget:
        # Houdini holds a tab bar's state on its first folder.
        name = _instanced(entries[0]["name"], instance)
        key = "tabs:" + name
        tabs = QtWidgets.QTabWidget()
        tabs.setDocumentMode(True)
        for position, entry in enumerate(entries):
            page = QtWidgets.QWidget()
            self._scopes.append(inside := set())
            self._add_entries(entry["children"], QtWidgets.QVBoxLayout(page), instance)
            self._scopes.pop()
            tabs.addTab(page, entry["label"])
            self._mark(
                inside,
                lambda changed, p=position: tabs.setTabIcon(
                    p, dot_icon() if changed else QtGui.QIcon()
                ),
            )
        tabs.setCurrentIndex(self._state.get(key, 0))
        tabs.currentChanged.connect(lambda i: self._state.__setitem__(key, i))
        self._flag(name, tabs.setEnabled, tabs.setVisible)
        return tabs

    def _multiparm(self, entry: Entry, instance: str) -> QtWidgets.QWidget:
        name = _instanced(entry["name"], instance)
        count = int(self.evaluated(name)[0])
        start = entry["start"]

        box = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(box)
        header = QtWidgets.QHBoxLayout()
        dot = ChangeDot()
        header.addWidget(dot)
        self._mark({name}, dot.set_changed)
        title = QtWidgets.QLabel(f"<b>{entry['label']}</b>  ({count})")
        header.addWidget(title)
        header.addStretch(1)
        for text, tip, target in (
            ("+", "Add one at the end", count + 1),
            ("−", "Remove the last one", max(count - 1, 0)),
            ("Clear", "Remove them all", 0),
        ):
            button = QtWidgets.QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setEnabled(target != count)
            button.clicked.connect(
                lambda _=False, n=target: self.edited.emit(name, [n])
            )
            header.addWidget(button)
        layout.addLayout(header)

        for index in range(start, start + count):
            frame = QtWidgets.QFrame()
            frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
            row = QtWidgets.QHBoxLayout(frame)
            number = QtWidgets.QLabel(str(index))
            number.setFixedWidth(24)
            number.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
            row.addWidget(number)
            content = QtWidgets.QVBoxLayout()
            self._add_entries(entry["children"], content, str(index))
            row.addLayout(content, 1)
            remove = QtWidgets.QToolButton()
            remove.setText("✕")
            remove.setToolTip(f"Remove {entry['label']} {index}")
            remove.clicked.connect(
                lambda _=False, i=index: self.instance_removed.emit(name, i - start)
            )
            row.addWidget(remove, 0, QtCore.Qt.AlignTop)
            layout.addWidget(frame)
        self._flag(name, box.setEnabled, box.setVisible)
        return box

    # ---- editing -------------------------------------------------------

    def _commit(
        self, entry: Entry, name: str, edits: list[QtWidgets.QLineEdit]
    ) -> None:
        current = self._parms[name].value
        value = list(current)
        for index, edit in enumerate(edits):
            edit.setModified(False)
            if edit.text() != _shown(current[index]):
                value[index] = _parsed(edit.text(), entry["type"], current[index])
        if value != current:
            self.edited.emit(name, value)
        else:
            for edit, old in zip(edits, current):
                edit.setText(_shown(old))

    def _pick(
        self,
        entry: Entry,
        name: str,
        edits: list[QtWidgets.QLineEdit],
        only: TypeFilter | None,
        single: bool,
    ) -> None:
        picker = PrimPicker(
            self,
            self._prims,
            edits[0].text().split(),
            f"Choose {entry['label']}",
            only,
            single,
        )
        if not picker.exec_():
            return
        chosen = picker.chosen()
        edits[0].setText(chosen[0] if single and chosen else " ".join(chosen))
        self._commit(entry, name, edits)

    def _picker_for(self, entry: Entry) -> tuple[TypeFilter | None, bool] | None:
        for prefix, picker in _TYPED_PICKERS.items():
            if entry["name"].startswith(prefix):
                return picker
        if entry["prim_path"]:
            return None, False
        return None

    # ---- states and change dots ----------------------------------------

    def _show(self, name: str, show: Show) -> None:
        if name in self._parms:
            self._shows.append((name, show))
            show(self._parms[name])

    def _flag(self, name: str, set_enabled: SetFlag, set_visible: SetFlag) -> None:
        if name in self._parms:
            self._flags.append((name, set_enabled, set_visible))

    def _mark(self, names: set[str], set_changed: SetFlag) -> None:
        """Show `set_changed` whether any of `names` changed, and count them as
        inside every folder and tab being built."""
        for scope in self._scopes:
            scope.update(names)
        self._marks.append((names, set_changed))

    def _refresh(self) -> None:
        for name, set_enabled, set_visible in self._flags:
            set_enabled(not self._parms[name].disabled)
            set_visible(not self._parms[name].hidden)
        changed = {
            name
            for name in self._parms.keys() | self._saved.keys()
            if name not in self._parms
            or self._parms[name].value != self._saved.get(name)
        }
        for names, set_changed in self._marks:
            set_changed(not names.isdisjoint(changed))


class _PathEdit(QtWidgets.QLineEdit):
    """A prim path field. Prims dragged from the scene graph are added after
    the paths already in it, rather than into the middle of one."""

    # Qt names the parameter arg__1, which an override must match.
    def dropEvent(self, arg__1: QtGui.QDropEvent) -> None:  # noqa: N802 (Qt's name)
        event = arg__1
        dropped = event.mimeData().text().split()
        if not dropped:
            return
        present = self.text().split()
        self.setText(" ".join(present + [p for p in dropped if p not in present]))
        event.acceptProposedAction()
        self.setModified(True)
        self.editingFinished.emit()


class _Section(QtWidgets.QWidget):
    """A collapsible folder: an arrow, an optional header toggle, and its parms."""

    expandedChanged = QtCore.Signal(bool)

    _arrow: QtWidgets.QToolButton
    _header: QtWidgets.QHBoxLayout
    _body: QtWidgets.QWidget | None
    _dot: ChangeDot

    def __init__(self, title: str, expanded: bool) -> None:
        super().__init__()
        self._body = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        bar = QtWidgets.QFrame()
        bar.setObjectName("sectionBar")
        bar.setStyleSheet(
            "#sectionBar { background-color: palette(button); border-radius: 3px; }"
        )
        self._header = QtWidgets.QHBoxLayout(bar)
        self._header.setContentsMargins(4, 2, 4, 2)
        self._arrow = QtWidgets.QToolButton()
        self._arrow.setText(title)
        self._arrow.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._arrow.setAutoRaise(True)
        self._arrow.setCheckable(True)
        self._arrow.setChecked(expanded)
        self._arrow.setStyleSheet("QToolButton { font-weight: bold; border: none; }")
        self._arrow.toggled.connect(self._expand)
        self._dot = ChangeDot()
        self._header.addWidget(self._dot)
        self._header.addWidget(self._arrow)
        self._header.addStretch(1)
        layout.addWidget(bar)
        self._set_arrow(expanded)

    def add_toggle(self) -> QtWidgets.QCheckBox:
        box = QtWidgets.QCheckBox()
        # After the dot, before the title.
        self._header.insertWidget(1, box)
        return box

    def set_changed(self, changed: bool) -> None:
        self._dot.set_changed(changed)

    def set_body(self, body: QtWidgets.QWidget) -> None:
        self._body = body
        body.setContentsMargins(16, 0, 0, 0)
        self.layout().addWidget(body)
        body.setVisible(self._arrow.isChecked())

    def _expand(self, expanded: bool) -> None:
        self._set_arrow(expanded)
        if self._body is not None:
            self._body.setVisible(expanded)
        self.expandedChanged.emit(expanded)

    def _set_arrow(self, expanded: bool) -> None:
        self._arrow.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
        )


def _is_tabs(entry: Entry) -> bool:
    return entry["type"] == FOLDER and entry["folder_type"] in TABS


def _run(
    entries: list[Entry], start: int, matches: Callable[[Entry], bool]
) -> list[Entry]:
    run = []
    for entry in entries[start:]:
        if not matches(entry):
            break
        run.append(entry)
    return run


def _joined(entries: list[Entry], start: int) -> list[Entry]:
    """The parm at `start` and those Join Next puts on its row."""
    row = [entries[start]]
    while row[-1].get("join_next") and start + len(row) < len(entries):
        following = entries[start + len(row)]
        if following["type"] == FOLDER:
            break
        row.append(following)
    return row


def _values(parms: dict[str, ParmState]) -> dict[str, list[Any]]:
    return {name: state.value for name, state in parms.items()}


def _instanced(name: str, instance: str) -> str:
    return name.replace(INSTANCE_TOKEN, instance, 1) if instance else name


def _tooltip(entry: Entry, instance: str) -> str:
    name = _instanced(entry["name"], instance)
    return (
        f"{entry['help']}\n\nParameter: {name}".strip()
        if entry["help"]
        else f"Parameter: {name}"
    )


def _shown(value: Any) -> str:
    if isinstance(value, dict):
        return str(value[EXPRESSION])
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _parsed(text: str, kind: str, old: Any) -> Any:
    """`text` as the parm's value: a number, else an HScript expression."""
    if kind == STRING:
        return text
    text = text.strip()
    if not text:
        return old
    try:
        number = float(text)
    except ValueError:
        return {EXPRESSION: text, LANGUAGE: HSCRIPT}
    return int(round(number)) if kind == INT else number


def _style(edit: QtWidgets.QLineEdit, value: Any, result: Any, tooltip: str) -> None:
    """Green for an expression, which a Python one makes read-only."""
    is_expression = isinstance(value, dict)
    is_python = is_expression and value[LANGUAGE] != HSCRIPT
    edit.setStyleSheet(_EXPRESSION_STYLE if is_expression else "")
    edit.setReadOnly(is_python)
    if is_python:
        edit.setToolTip("A Python expression drives this. Change it in Houdini.")
    elif is_expression:
        edit.setToolTip(
            f"{_shown(value)} = {_shown(result)}\nType a value to replace the expression."
        )
    else:
        edit.setToolTip(tooltip)
