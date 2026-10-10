"""The "Split Pieces" as a read-only table."""

from __future__ import annotations

import logging

from maya import cmds as mc
from Qt import QtGui, QtWidgets

from pipe.core.asset.naming import Adopt, New, Occupied
from pipe.core.assembly.model import AssemblyError, Piece
from pipe.core.assembly.plan import AddVariant, Plan
from pipe.core.shotgrid import Asset, ShotGrid
from pipe.core.ui import FAIL, FAIL_STYLE, MessageDialog, progress_scope
from pipe.dcc.maya.assembly.run import RunReport, run_split
from pipe.dcc.maya.assembly.scan import plan_split
from pipe.dcc.maya.assetfile import scene_asset

log = logging.getLogger(__name__)

_COLUMNS = ("Group", "Asset", "Variant", "Outcome")
_RUN_STEP = "Splitting pieces"
_REFUSED = "Refused"
_REFUSALS_HEADING = "Split is disabled until these are fixed:"


class SplitDialog(QtWidgets.QDialog):
    def __init__(
        self, parent: QtWidgets.QWidget | None, conn: ShotGrid, assembly: Asset
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._assembly = assembly
        self._plan: Plan | None = None
        self.setWindowTitle(f"Split Pieces - {assembly.display_name}")
        self.setMinimumSize(720, 360)

        layout = QtWidgets.QVBoxLayout(self)
        self._table = QtWidgets.QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QtWidgets.QDialogButtonBox()
        refresh = buttons.addButton("Refresh", QtWidgets.QDialogButtonBox.ResetRole)
        self._split = buttons.addButton("Split", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        refresh.clicked.connect(self.refresh)
        buttons.accepted.connect(self._confirm_and_run)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh()

    def refresh(self) -> None:
        """Plan again from the scene, ShotGrid and disk, and show the result."""
        try:
            self._refuse_other_scene()
            self._plan = plan_split(self._assembly, self._conn.find_assets())
        except AssemblyError as error:
            self._plan = None
            self._show_rows([])
            self._show_status([str(error)], failed=True)
            return
        except Exception:
            log.exception("Split Pieces could not plan for %s", self._assembly.name)
            self._plan = None
            self._show_rows([])
            self._show_status(
                ["The plan could not be made; the Script Editor has the details."],
                failed=True,
            )
            return
        plan = self._plan
        self._show_rows(
            [(child, piece) for child in plan.children for piece in child.pieces]
        )
        problems = [refusal.reason for refusal in plan.refusals]
        if problems:
            self._show_status([_REFUSALS_HEADING, *problems], failed=True)
        elif not plan.pieces:
            self._show_status(["No unsplit groups."], failed=False)
        else:
            self._show_status([], failed=False)

    def _show_rows(self, rows: list[tuple]) -> None:
        plan = self._plan
        self._table.setRowCount(len(rows))
        for index, (child, piece) in enumerate(rows):
            reasons = plan.refused(piece.name) if plan is not None else []
            outcome = _REFUSED if reasons else _outcome(child.claim)
            cells = (piece.name, child.display_name, piece.variant, outcome)
            for column, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if reasons:
                    item.setForeground(QtGui.QColor(FAIL))
                    item.setToolTip("\n".join(reasons))
                self._table.setItem(index, column, item)
            if not reasons:
                self._table.item(index, 1).setToolTip(child.asset_path)
        self._table.resizeColumnsToContents()

    def _show_status(self, lines: list[str], *, failed: bool) -> None:
        self._status.setText("\n".join(lines))
        self._status.setVisible(bool(lines))
        self._status.setStyleSheet(FAIL_STYLE if failed else "")
        self._split.setEnabled(self._plan is not None and self._plan.ready)

    def _confirm_and_run(self) -> None:
        """Ask once, split, report, then show what is left."""
        plan = self._plan
        if plan is None or not plan.ready:
            return
        try:
            self._refuse_other_scene()
        except AssemblyError as error:
            MessageDialog(self, str(error), "Cannot split").exec_()
            self.refresh()
            return
        answer = mc.confirmDialog(
            title="Split Pieces",
            message=(
                f"Split {len(plan.pieces)} pieces into {len(plan.children)} assets?\n\n"
                "This cannot be undone. A version of the scene is kept first."
            ),
            button=["Split", "Cancel"],
            defaultButton="Split",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if answer != "Split":
            return
        try:
            report = self._run(plan)
        except AssemblyError as error:
            MessageDialog(self, str(error), "Cannot split").exec_()
            self.refresh()
            return
        except Exception:
            log.exception("Split Pieces stopped unexpectedly")
            MessageDialog(
                self,
                "Split Pieces stopped for a reason the tool did not expect; the "
                "Script Editor has the details. Every piece split before the stop "
                "is saved, and the plan below shows what is left.",
                "Split Pieces",
            ).exec_()
            self.refresh()
            return
        MessageDialog(self, _report_text(report), "Split Pieces").exec_()
        self.refresh()

    def _refuse_other_scene(self) -> None:
        """The plan and the run read the open scene, which must still be this
        assembly's: the dialog outlives Open Asset."""
        current = scene_asset(self._conn)
        if current == self._assembly:
            return
        name = current.display_name if current is not None else "a different scene"
        raise AssemblyError(
            f"This dialog splits '{self._assembly.display_name}', but the open scene "
            f"is now {name}. Close this dialog, open the assembly's scene, and press "
            "Split Pieces again."
        )

    def _run(self, plan: Plan) -> RunReport:
        total = len(plan.pieces)
        done = 0
        with progress_scope(
            parent=self, title="Split Pieces", steps=[_RUN_STEP]
        ) as progress:
            progress.begin_step(_RUN_STEP, "Keeping a version of the scene")

            def on_piece(piece: Piece) -> None:
                nonlocal done
                progress.update_substep(done, total, piece.name)
                done += 1

            return run_split(self._conn, self._assembly, plan, on_piece=on_piece)


def _outcome(claim: New | Adopt | AddVariant | Occupied) -> str:
    if isinstance(claim, New):
        return "New asset"
    if isinstance(claim, Adopt):
        return "Adopt record"
    if isinstance(claim, AddVariant):
        if claim.split_from:
            return f"Add variant (split from {claim.split_from})"
        return "Add variant"
    return _REFUSED


def _report_text(report: RunReport) -> str:
    stop = report.stop
    if stop is None:
        return (
            f"Split {report.split} pieces. Version {report.version} holds the "
            "scene from before.\n\nPress Publish to build the children."
        )
    before = (
        f"The {report.split} pieces before it are split and saved."
        if report.split
        else "Nothing was split."
    )
    return f"Stopped at '{stop.group}': {stop.reason}\n\n{before}"
