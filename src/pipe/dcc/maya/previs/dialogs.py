"""Modal pickers, inline menus, and warning popups for the previs panel."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from Qt.QtGui import QCursor
from Qt.QtWidgets import (
    QDialog,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
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
