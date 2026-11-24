"""The Render Network's parms as Qt widgets, drawn from the schema the worker sends.

The form owns the values. Each widget writes its parm's value as the artist
edits it, and every edit re-reads the Disable When and Hide When conditions.
Adding or removing a multiparm instance rebuilds the form, keeping which
folders are open and which tabs are chosen.

A yellow dot marks each row, collapsed folder and tab whose parms differ from
the values the form opened with, which are the hip's as last saved.

Multiparms inside multiparms are not supported; the Render Network has none.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.app.conditions import UNKNOWN, MenuValue, holds
from pipe.core.render.app.marks import ChangeDot, dot_icon
from pipe.core.render.app.scene_graph import (
    PrimPicker,
    TypeFilter,
    is_camera,
    is_light,
)
from pipe.core.render.protocol import (
    BUTTON,
    COLLAPSIBLE,
    EXPRESSION,
    EXPRESSION_LANGUAGE,
    FOLDER,
    HSCRIPT,
    INSTANCE_TOKEN,
    INT,
    LABEL,
    MENU,
    MULTIPARM,
    PARM,
    SEPARATOR,
    STRING,
    TABS,
    TOGGLE,
    Prim,
    Values,
)

Entry = dict[str, Any]

LABEL_WIDTH = 170
NUMBER_WIDTH = 90
# The window has its own Send button.
_SKIPPED_TYPES = (BUTTON,)
_BLANK_LOOK = "blank"
_EXPRESSION_STYLE = "QLineEdit { background-color: #2d4a32; }"
_NOTE_STYLE = "QLabel { background-color: palette(alternate-base); border-radius: 4px; padding: 8px; }"

# Parms the asset doesn't tag as prim paths, but which name prims of one kind:
# (picker filter, whether only one prim makes sense).
_TYPED_PICKERS: dict[str, tuple[TypeFilter, bool]] = {
    "camOverride": (is_camera, True),
    "lightlayer_lights": (is_light, False),
}


@dataclass
class _Conditional:
    disable_when: str
    hide_when: str
    set_enabled: Callable[[bool], None]
    set_visible: Callable[[bool], None]


class ParmForm(QtWidgets.QWidget):
    """The parms of `schema`, editing a copy of `values`."""

    changed = QtCore.Signal()

    _schema: list[Entry]
    _values: Values
    _prims: Sequence[Prim]
    # What the expressions evaluated to when Houdini last opened the hip.
    _evaluated: Values
    # Folder and tab-bar states by name, kept across rebuilds and reloads.
    _state: dict[str, Any]
    _menus: dict[str, list[str]]
    # Component names such as `f1` → (tuple name, index), for conditions.
    _components: dict[str, tuple[str, int]]
    _conditionals: list[_Conditional]
    _body: QtWidgets.QWidget | None
    # The values as opened, which the change dots compare against.
    _baseline: Values | None
    # Each dot's parm names, and how to show whether any of them changed.
    _marks: list[tuple[set[str], Callable[[bool], None]]]
    # The names of the folders and tabs being built, innermost last.
    _scopes: list[set[str]]

    def __init__(
        self,
        schema: list[Entry],
        values: Values,
        prims: Sequence[Prim],
        evaluated: Values | None = None,
        state: dict[str, Any] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._schema = schema
        self._values = copy.deepcopy(values)
        self._evaluated = evaluated or {}
        self._prims = prims
        self._state = state if state is not None else {}
        self._body, self._baseline = None, None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._build()
        # After building, which fills in the defaults of parms the hip lacks.
        self._baseline = copy.deepcopy(self._values)

    def values(self) -> Values:
        return copy.deepcopy(self._values)

    def state(self) -> dict[str, Any]:
        return self._state

    def value(self, name: str) -> Any:
        """The first component of `name`, or None when the form has no such parm."""
        found = self._values.get(name)
        return found[0] if found else None

    # ---- building ------------------------------------------------------

    def _build(self) -> None:
        if self._body is not None:
            self._body.hide()
            self._body.deleteLater()
        self._menus, self._components, self._conditionals = {}, {}, []
        self._marks, self._scopes = [], []
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
            if entry["kind"] == FOLDER and entry["style"] == TABS:
                run = _run(
                    entries, index, lambda e: e["kind"] == FOLDER and e["style"] == TABS
                )
                layout.addWidget(self._tabs(run, instance))
                index += len(run)
            elif entry["kind"] == PARM:
                row = _joined(entries, index)
                index += len(row)
                row = [
                    e
                    for e in row
                    if e["name"] not in headers and e["type"] not in _SKIPPED_TYPES
                ]
                if row:
                    layout.addWidget(self._row(row, instance))
            elif entry["kind"] == FOLDER:
                layout.addWidget(self._folder(entry, instance))
                index += 1
            else:
                layout.addWidget(self._multiparm(entry, instance))
                index += 1

    def _row(self, entries: list[Entry], instance: str) -> QtWidgets.QWidget:
        first = entries[0]
        if first["type"] == SEPARATOR:
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Sunken)
            self._watch(first, instance, line.setEnabled, line.setVisible)
            return line
        if first["type"] == LABEL and len(entries) == 1:
            note = QtWidgets.QLabel(first.get("text", ""))
            note.setWordWrap(True)
            note.setStyleSheet(_NOTE_STYLE)
            self._watch(first, instance, note.setEnabled, note.setVisible)
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
            if entry is not named and entry["type"] != TOGGLE and not entry["no_label"]:
                inline = QtWidgets.QLabel(entry["label"])
                layout.addWidget(inline)
                self._watch(entry, instance, inline.setEnabled, inline.setVisible)
            layout.addWidget(self._field(entry, instance))
        layout.addStretch(1)
        self._watch(named, instance, title.setEnabled, row.setVisible)
        return row

    def _field(self, entry: Entry, instance: str) -> QtWidgets.QWidget:
        name = _instanced(entry["name"], instance)
        current = self._values.setdefault(name, copy.deepcopy(entry.get("default", [])))
        if entry["size"] > 1:
            for index in range(entry["size"]):
                self._components[f"{name}{index + 1}"] = (name, index)

        kind = entry["type"]
        if kind == TOGGLE:
            field: QtWidgets.QWidget = self._toggle(entry, name, current)
        elif kind == MENU:
            field = self._menu(entry, name, current)
        elif kind == STRING:
            field = self._strings(entry, name, current)
        elif kind == LABEL:
            field = QtWidgets.QLabel(entry.get("text", ""))
        else:
            field = self._numbers(entry, name, current)
        if not field.toolTip():
            field.setToolTip(_tooltip(entry, instance))
        self._watch(entry, instance, field.setEnabled, field.setVisible)
        return field

    def _toggle(
        self, entry: Entry, name: str, current: list[Any]
    ) -> QtWidgets.QCheckBox:
        box = QtWidgets.QCheckBox("" if entry["no_label"] else entry["label"])
        box.setChecked(
            bool(current[0]) if current and not isinstance(current[0], dict) else False
        )
        box.toggled.connect(lambda on: self._set(name, [on]))
        return box

    def _menu(self, entry: Entry, name: str, current: list[Any]) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        tokens = [token for token, _ in entry["menu"]]
        self._menus[name] = tokens
        for token, label in entry["menu"]:
            combo.addItem(label, token)
        token = current[0] if current else ""
        if token not in tokens:
            # A value the menu lacks, such as an expression's; kept until changed.
            combo.addItem(str(token), token)
        combo.setCurrentIndex(combo.findData(token))
        combo.currentIndexChanged.connect(
            lambda i: self._set(name, [combo.itemData(i)])
        )
        return combo

    def _strings(
        self, entry: Entry, name: str, current: list[Any]
    ) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        picker = self._picker_for(entry)
        edits = []
        for index in range(entry["size"]):
            text = _shown(current[index] if index < len(current) else "")
            edit = _PathEdit(text) if picker is not None else QtWidgets.QLineEdit(text)
            edit.setMinimumWidth(320)
            edit.textEdited.connect(lambda _: self._set_strings(name, edits))
            edits.append(edit)
            layout.addWidget(edit, 1)
        if picker is not None:
            only, single = picker
            button = QtWidgets.QToolButton()
            button.setText("…")
            button.setToolTip("Choose prims from the shot's stage")
            button.clicked.connect(
                lambda: self._pick(entry, name, edits[0], only, single)
            )
            layout.addWidget(button)
        return holder

    def _numbers(
        self, entry: Entry, name: str, current: list[Any]
    ) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        for index in range(entry["size"]):
            value = current[index] if index < len(current) else 0
            edit = QtWidgets.QLineEdit(_shown(value))
            edit.setFixedWidth(NUMBER_WIDTH)
            _style_expression(edit, value)
            if isinstance(value, dict) and name in self._evaluated:
                edit.setToolTip(
                    f"{_shown(value)} = {_shown(self._evaluated[name][index])}\n"
                    "Type a number to replace the expression."
                )
            edit.editingFinished.connect(
                lambda e=edit, i=index: self._finish_number(entry, name, i, e)
            )
            layout.addWidget(edit)
        return holder

    def _folder(self, entry: Entry, instance: str) -> QtWidgets.QWidget:
        key = _instanced(entry["name"], instance)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        self._scopes.append(inside := set())
        self._add_entries(entry["children"], body_layout, instance)
        self._scopes.pop()

        if entry["style"] != COLLAPSIBLE:
            if entry.get("look") == _BLANK_LOOK:
                body_layout.setContentsMargins(0, 0, 0, 0)
                self._watch(entry, instance, body.setEnabled, body.setVisible)
                return body
            box = QtWidgets.QGroupBox(entry["label"])
            QtWidgets.QVBoxLayout(box).addWidget(body)
            self._watch(entry, instance, body.setEnabled, box.setVisible)
            return box

        toggle_name = (
            _instanced(entry["header_toggle"], instance)
            if entry["header_toggle"]
            else ""
        )
        on = bool(self.value(toggle_name)) if toggle_name else False
        section = _Section(entry["label"], self._state.get(key, on))
        section.expandedChanged.connect(
            lambda open_: self._state.__setitem__(key, open_)
        )
        if toggle_name:
            section.add_toggle(on, lambda checked: self._set(toggle_name, [checked]))
        section.set_body(body)
        # Its dot shows changes to parms hidden while it's closed.
        self._mark(inside | {toggle_name} - {""}, section.set_changed)
        self._watch(entry, instance, body.setEnabled, section.setVisible)
        return section

    def _tabs(self, entries: list[Entry], instance: str) -> QtWidgets.QTabWidget:
        key = "tabs:" + _instanced(entries[0]["name"], instance)
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
            self._watch(
                entry,
                instance,
                lambda on, p=position: tabs.setTabEnabled(p, on),
                lambda on, p=position: tabs.setTabVisible(p, on),
            )
        tabs.setCurrentIndex(self._state.get(key, 0))
        tabs.currentChanged.connect(lambda i: self._state.__setitem__(key, i))
        return tabs

    def _multiparm(self, entry: Entry, instance: str) -> QtWidgets.QWidget:
        name = _instanced(entry["name"], instance)
        count = int(self._values.setdefault(name, [0])[0])
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
            button.clicked.connect(
                lambda _=False, n=target: self._resize(entry, name, n)
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
                lambda _=False, i=index: self._remove(entry, name, i)
            )
            row.addWidget(remove, 0, QtCore.Qt.AlignTop)
            layout.addWidget(frame)
        self._watch(entry, instance, box.setEnabled, box.setVisible)
        return box

    # ---- editing -------------------------------------------------------

    def _set(self, name: str, value: list[Any]) -> None:
        self._values[name] = value
        self._refresh()
        self.changed.emit()

    def _set_strings(self, name: str, edits: list[QtWidgets.QLineEdit]) -> None:
        self._set(name, [edit.text() for edit in edits])

    def _finish_number(
        self, entry: Entry, name: str, index: int, edit: QtWidgets.QLineEdit
    ) -> None:
        current = self._values[name]
        parsed = _parsed(edit.text(), entry)
        if parsed is None:
            edit.setText(_shown(current[index]))
            return
        edit.setText(_shown(parsed))
        _style_expression(edit, parsed)
        if parsed == current[index]:
            return
        updated = list(current)
        updated[index] = parsed
        self._set(name, updated)

    def _pick(
        self,
        entry: Entry,
        name: str,
        edit: QtWidgets.QLineEdit,
        only: TypeFilter | None,
        single: bool,
    ) -> None:
        picker = PrimPicker(
            self,
            self._prims,
            edit.text().split(),
            f"Choose {entry['label']}",
            only,
            single,
        )
        if not picker.exec_():
            return
        chosen = picker.chosen()
        edit.setText(chosen[0] if single and chosen else " ".join(chosen))
        self._set(name, [edit.text()])

    def _resize(self, entry: Entry, name: str, count: int) -> None:
        start, old = entry["start"], int(self._values[name][0])
        for index in range(start + old, start + count):
            for child in _leaves(entry["children"]):
                self._values[_instanced(child["name"], str(index))] = copy.deepcopy(
                    child.get("default", [])
                )
        for index in range(start + count, start + old):
            self._forget(entry, index)
        self._values[name] = [count]
        self._rebuild()

    def _remove(self, entry: Entry, name: str, removed: int) -> None:
        start, count = entry["start"], int(self._values[name][0])
        last = start + count - 1
        # The instances after it move up one, as Houdini renumbers them.
        for index in range(removed, last):
            for child in _leaves(entry["children"]):
                after = self._values.get(_instanced(child["name"], str(index + 1)))
                if after is not None:
                    self._values[_instanced(child["name"], str(index))] = after
        self._forget(entry, last)
        self._values[name] = [count - 1]
        self._rebuild()

    def _forget(self, entry: Entry, index: int) -> None:
        for child in _leaves(entry["children"]):
            self._values.pop(_instanced(child["name"], str(index)), None)

    def _rebuild(self) -> None:
        # Not from inside the clicked button's own signal, which _build deletes.
        QtCore.QTimer.singleShot(0, self._build)
        self.changed.emit()

    # ---- change dots ---------------------------------------------------

    def _mark(self, names: set[str], set_changed: Callable[[bool], None]) -> None:
        """Show `set_changed` whether any of `names` changed, and count them as
        inside every folder and tab being built."""
        for scope in self._scopes:
            scope.update(names)
        self._marks.append((names, set_changed))

    def _refresh_marks(self) -> None:
        if self._baseline is None:
            return
        baseline = self._baseline
        changed = {
            name
            for name in self._values.keys() | baseline.keys()
            if self._values.get(name) != baseline.get(name)
        }
        for names, set_changed in self._marks:
            set_changed(not names.isdisjoint(changed))

    # ---- conditions ----------------------------------------------------

    def _watch(
        self,
        entry: Entry,
        instance: str,
        set_enabled: Callable[[bool], None],
        set_visible: Callable[[bool], None],
    ) -> None:
        disable_when = _instanced_all(entry.get("disable_when", ""), instance)
        hide_when = _instanced_all(entry.get("hide_when", ""), instance)
        if disable_when or hide_when:
            self._conditionals.append(
                _Conditional(disable_when, hide_when, set_enabled, set_visible)
            )

    def _refresh(self) -> None:
        self._refresh_marks()
        for conditional in self._conditionals:
            if conditional.disable_when:
                conditional.set_enabled(
                    not holds(conditional.disable_when, self._lookup, if_unknown=False)
                )
            if conditional.hide_when:
                conditional.set_visible(
                    not holds(conditional.hide_when, self._lookup, if_unknown=True)
                )

    def _lookup(self, name: str) -> Any:
        if name in self._components:
            tuple_name, index = self._components[name]
            value = self._values[tuple_name][index]
        elif self._values.get(name):
            value = self._values[name][0]
        else:
            return UNKNOWN
        if isinstance(value, dict):
            return UNKNOWN
        if name in self._menus:
            return MenuValue(value, self._menus[name])
        return value

    def _picker_for(self, entry: Entry) -> tuple[TypeFilter | None, bool] | None:
        for prefix, picker in _TYPED_PICKERS.items():
            if entry["name"].startswith(prefix):
                return picker
        if entry.get("prim_path"):
            return None, False
        return None


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
        # setText isn't an edit; the form listens for edits.
        self.textEdited.emit(self.text())


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

    def add_toggle(self, on: bool, toggled: Callable[[bool], None]) -> None:
        box = QtWidgets.QCheckBox()
        box.setChecked(on)
        box.toggled.connect(toggled)
        # After the dot, before the title.
        self._header.insertWidget(1, box)

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
        if following["kind"] != PARM:
            break
        row.append(following)
    return row


def _leaves(entries: list[Entry]) -> list[Entry]:
    """The value-holding parms below `entries`, through folders."""
    found = []
    for entry in entries:
        if entry["kind"] == FOLDER:
            found += _leaves(entry["children"])
        elif entry["kind"] == MULTIPARM or entry["type"] not in (
            LABEL,
            BUTTON,
            SEPARATOR,
        ):
            found.append(entry)
    return found


def _instanced(name: str, instance: str) -> str:
    return name.replace(INSTANCE_TOKEN, instance, 1) if instance else name


def _instanced_all(condition: str, instance: str) -> str:
    return condition.replace(INSTANCE_TOKEN, instance) if instance else condition


def _tooltip(entry: Entry, instance: str) -> str:
    name = _instanced(entry["name"], instance)
    return (
        f"{entry['help']}\n\nParameter: {name}".strip()
        if entry.get("help")
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


def _parsed(text: str, entry: Entry) -> Any:
    """`text` as the parm's number, or as an HScript expression, or None to
    keep the old value."""
    text = text.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return {EXPRESSION: text, EXPRESSION_LANGUAGE: HSCRIPT}
    if entry.get("min_strict"):
        number = max(number, entry["min"])
    if entry.get("max_strict"):
        number = min(number, entry["max"])
    return int(round(number)) if entry["type"] == INT else number


def _style_expression(edit: QtWidgets.QLineEdit, value: Any) -> None:
    is_expression = isinstance(value, dict)
    edit.setStyleSheet(_EXPRESSION_STYLE if is_expression else "")
    if is_expression and value.get(EXPRESSION_LANGUAGE, HSCRIPT) != HSCRIPT:
        edit.setReadOnly(True)
        edit.setToolTip("A Python expression drives this. Change it in Houdini.")
