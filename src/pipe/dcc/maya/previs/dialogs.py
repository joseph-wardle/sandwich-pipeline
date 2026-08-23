"""Modal pickers, inline menus, and warning popups for the previs panel."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from Qt.QtGui import QCursor
from Qt.QtCore import Qt
from Qt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.previs import naming
from pipe.core.ui import DialogButtons, FilteredListDialog, MessageDialogCustomButtons

if TYPE_CHECKING:
    from pipe.core.previs.model import FileRecord

    from .rlo import DeliveryPlan


def pick_scene_camera(parent: QWidget, candidates: Sequence[str]) -> str | None:
    """Prompt the user to pick an existing camera namespace from `candidates`."""
    if not candidates:
        return None
    dialog = FilteredListDialog(
        parent,
        list(candidates),
        title="Pick Existing Camera",
        list_label="Select a camera namespace to add to this shot:",
        accept_button_name="Add",
    )
    if not dialog.exec_():
        return None
    return dialog.get_selected_item()


# The picker lists real workspace files plus this synthetic row; selecting it
# means "start a new file" rather than "open an existing one".
_NEW_FILE_ROW = "＋  New file…"


@dataclass(frozen=True)
class WorkspaceChoice:
    """The artist's pick from pick_workspace_file.

    filename is the file to open, or None to start a new one (the "New file…"
    row). A cancelled dialog returns None instead of a choice.
    """

    filename: str | None


def _format_workspace_row(record: FileRecord) -> str:
    """A picker row for one file: ``label · v003 · 2026-07-13 · 4 shots``."""
    date = record.created_at.split("T", 1)[0] if record.created_at else "—"
    count = len(record.shot_codes)
    shots = "1 shot" if count == 1 else f"{count} shots"
    return f"{record.label} · {naming.version_token(record.version)} · {date} · {shots}"


def pick_workspace_file(
    parent: QWidget | None,
    records: Sequence[FileRecord],
    *,
    sequence_code: str,
) -> WorkspaceChoice | None:
    """Pick an existing workspace file or the "New file…" row; None on cancel."""
    rows = [_format_workspace_row(r) for r in records]
    filename_by_row = {row: r.filename for row, r in zip(rows, records)}
    dialog = FilteredListDialog(
        parent,
        [*rows, _NEW_FILE_ROW],
        title="Open Previs File",
        list_label=f"Select a file in sequence {sequence_code}, or start a new one:",
        accept_button_name="Open",
    )
    if not dialog.exec_():
        return None
    selected = dialog.get_selected_item()
    if selected is None:
        return None
    if selected == _NEW_FILE_ROW:
        return WorkspaceChoice(filename=None)
    return WorkspaceChoice(filename=filename_by_row[selected])


def prompt_new_label(parent: QWidget | None) -> str | None:
    """Prompt for a new file's label

    Returns the raw entered text, or None on cancel or empty input. file_ops.new_file
    validates the label and surfaces any problem.
    """
    text, ok = QInputDialog.getText(
        parent, "New previs file", "File label (e.g. blocking):"
    )
    if not ok:
        return None
    text = text.strip()
    if not text:
        return None
    return text


@dataclass(frozen=True)
class BranchRequest:
    """A confirmed branch from prompt_branch."""

    note: str
    new_label: str | None


class _BranchDialog(QDialog, DialogButtons):
    """Note + optional new-stream label; a blank label keeps the current stream."""

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Branch", "Cancel")
        self.setWindowTitle("Branch Previs File")

        self._note = QLineEdit()
        self._note.setPlaceholderText("why you're branching (optional)")
        self._new_label = QLineEdit()
        self._new_label.setPlaceholderText("blank keeps the current stream")

        form = QFormLayout()
        form.addRow("Note:", self._note)
        form.addRow("New label:", self._new_label)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def request(self) -> BranchRequest:
        label = self._new_label.text().strip()
        return BranchRequest(note=self._note.text().strip(), new_label=label or None)


def prompt_branch(parent: QWidget | None) -> BranchRequest | None:
    """Prompt for a branch note and optional new stream label; None on cancel.

    A blank label continues the current file's stream; a filled-in label starts a
    new stream. file_ops.branch_current validates the label and surfaces any problem.
    """
    dialog = _BranchDialog(parent)
    if not dialog.exec_():
        return None
    return dialog.request()


def prompt_rename(parent: QWidget, current: str) -> str | None:
    """Prompt for a new namespace name; return None on cancel or unchanged."""
    new_name, ok = QInputDialog.getText(
        parent, "Rename camera", "New namespace:", text=current
    )
    if not ok:
        return None
    new_name = new_name.strip()
    if not new_name or new_name == current:
        return None
    return new_name


def prompt_shot_code(parent: QWidget, *, current: str, suggestion: str) -> str | None:
    """Prompt for a shot's sticky code, prefilled with `current` or `suggestion`.

    Returns the raw entered text, or None on cancel, empty input, or an
    unchanged value.
    """
    prefill = current or suggestion
    text, ok = QInputDialog.getText(
        parent, "Set shot code", "Shot code (e.g. A_010):", text=prefill
    )
    if not ok:
        return None
    text = text.strip()
    if not text or text == current:
        return None
    return text


def show_add_alternate_menu(
    anchor: QWidget,
    *,
    on_new_rig: Callable[[], None],
    on_duplicate: Callable[[], None],
    on_existing: Callable[[], None],
) -> None:
    menu = QMenu(anchor)
    menu.addAction("New rig reference", on_new_rig)
    menu.addAction("Duplicate from primary", on_duplicate)
    menu.addAction("Pick existing camera…", on_existing)
    menu.exec_(QCursor.pos())


def confirm_delete_shot(
    parent: QWidget, *, label: str, namespaces: Sequence[str], undoable: bool
) -> bool:
    """Confirm deleting a shot, naming the cameras that go with it."""
    lines = [f"Delete {label}?", ""]
    if namespaces:
        lines.append("Its cameras are removed from the scene too:")
        lines.extend(f"  • {ns}" for ns in namespaces)
    else:
        lines.append("None of its cameras are still in the scene.")
    if not undoable:
        lines += [
            "",
            "Maya clears the undo queue when a referenced rig is removed, so this "
            "cannot be undone.",
        ]
    dialog = MessageDialogCustomButtons(
        parent,
        "\n".join(lines),
        "Delete Shot",
        has_cancel_button=True,
        ok_name="Delete",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def confirm_break_out(parent: QWidget, plan: DeliveryPlan) -> bool:
    """The single confirm break-out shows, whatever the delivery turns out to do."""
    dialog = MessageDialogCustomButtons(
        parent,
        _break_out_message(plan),
        "Break Out Shot",
        has_cancel_button=True,
        ok_name="Break out",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _break_out_message(plan: DeliveryPlan) -> str:
    """Name every consequence of `plan`, so one confirm covers all of them."""
    lines = [
        f"Break out {plan.code} — {plan.frames} frames, "
        f"{plan.cut_in}–{plan.cut_out}.",
        "",
    ]
    if plan.sg_shot is None:
        lines.append(
            f"  • Create shot {plan.code} in ShotGrid, in sequence "
            f"{plan.sequence.code}, with its standard task list."
        )
    elif plan.recuts:
        lines.append(
            f"  • Re-cut {plan.code} in ShotGrid to " f"{plan.cut_in}–{plan.cut_out}."
        )
    if plan.replaces_rlo:
        lines.append(
            f"  • Replace {plan.destination.name}, keeping the one already "
            "there as a version."
        )
    else:
        lines.append(f"  • Write {plan.destination.name}.")
    lines.append(
        "  • Save this previs file, and reopen it when the break-out finishes."
    )
    return "\n".join([*lines, "", _sets_note(plan)])


def _sets_note(plan: DeliveryPlan) -> str:
    """What the delivered RLO will be dressed with, when previs shows otherwise."""
    if plan.previs_sets == plan.rlo_sets:
        return (
            "Set dressing you moved in previs stays in previs — the RLO "
            "composes its sets from ShotGrid."
        )
    return (
        f"Heads up: previs is laid out against {_set_list(plan.previs_sets)}, but "
        f"{plan.code}'s RLO will compose {_set_list(plan.rlo_sets)} from ShotGrid."
    )


def _set_list(codes: tuple[str, ...]) -> str:
    return ", ".join(codes) if codes else "no set"


@dataclass(frozen=True)
class PlayblastRow:
    """One line of the playblast checklist."""

    shot_id: str
    label: str
    detail: str
    blocker: str | None


class _PlayblastChecklist(QDialog, DialogButtons):
    """Pick which shots to render. Everything renderable starts checked —
    unlike break-out, a playblast writes nothing until the viewer confirms it."""

    def __init__(self, parent: QWidget | None, rows: Sequence[PlayblastRow]) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Playblast", "Cancel")
        self.setWindowTitle("Playblast Shots")
        self.setMinimumWidth(420)

        self._list = QListWidget()
        for row in rows:
            self._list.addItem(_playblast_item(row))
        self._list.itemChanged.connect(self._refresh_ok)

        all_button = QPushButton("All")
        all_button.clicked.connect(lambda: self._set_all(True))
        none_button = QPushButton("None")
        none_button.clicked.connect(lambda: self._set_all(False))
        select = QHBoxLayout()
        select.addWidget(all_button)
        select.addWidget(none_button)
        select.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Shots to render, in cut order:"))
        layout.addWidget(self._list)
        layout.addLayout(select)
        layout.addWidget(self.buttons)
        self._refresh_ok()

    def chosen_ids(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._items()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def _items(self) -> list[QListWidgetItem]:
        return [self._list.item(row) for row in range(self._list.count())]

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._items():
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(state)

    def _refresh_ok(self) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.chosen_ids()))


def _playblast_item(row: PlayblastRow) -> QListWidgetItem:
    text = f"{row.label}  ·  {row.detail}"
    item = QListWidgetItem(text if row.blocker is None else f"{text}  —  {row.blocker}")
    item.setData(Qt.ItemDataRole.UserRole, row.shot_id)
    if row.blocker is None:
        item.setCheckState(Qt.CheckState.Checked)
        return item
    item.setCheckState(Qt.CheckState.Unchecked)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
    item.setToolTip(row.blocker)
    return item


def pick_shots_to_playblast(
    parent: QWidget | None, rows: Sequence[PlayblastRow]
) -> list[str] | None:
    """The shot ids to render, or None on cancel."""
    dialog = _PlayblastChecklist(parent, rows)
    if not dialog.exec_():
        return None
    return dialog.chosen_ids()
