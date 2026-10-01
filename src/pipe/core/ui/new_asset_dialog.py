"""Asks for a new asset: a name nobody holds, or the empty record already holding it."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

from Qt import QtCore, QtWidgets

from pipe.core.asset.naming import (
    Adopt,
    New,
    Occupied,
    classify,
    name_problem,
    subdirectories_in_use,
    subdirectory_problem,
)
from pipe.core.shotgrid import Asset
from pipe.core.shotgrid.paths import build_asset_path, normalize_display_name
from pipe.core.ui.style import FAIL_STYLE, OK_STYLE, WARN_STYLE


def ask_new_asset(
    parent: QtWidgets.QWidget | None,
    assets: Sequence[Asset],
    *,
    title: str,
    accept_label: str,
    production_root: Path | None = None,
) -> New | Adopt | None:
    """Ask which asset to make or reuse, or `None` if the artist cancels.

    `assets` is the caller's `find_assets()`; the dialog reads nothing from
    ShotGrid and creates nothing.
    """
    dialog = NewAssetDialog(parent, assets, title, accept_label, production_root)
    dialog.exec_()
    return dialog.choice


class NewAssetDialog(QtWidgets.QDialog):
    choice: New | Adopt | None

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        assets: Sequence[Asset],
        title: str,
        accept_label: str,
        production_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(460)

        self.choice = None
        self._assets = list(assets)
        self._in_use = subdirectories_in_use(self._assets)
        self._production_root = production_root
        self._claim: New | Adopt | None = None

        self._name = QtWidgets.QLineEdit()
        self._name.setPlaceholderText("Kitchen Knife")
        self._derived = QtWidgets.QLabel()

        self._subdirectory = QtWidgets.QComboBox()
        self._subdirectory.setEditable(True)
        # A folder typed here is judged as the artist types, never added to the menu.
        self._subdirectory.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self._subdirectory.addItems(sorted(self._in_use))
        self._subdirectory.setCurrentIndex(-1)

        self._rigged = QtWidgets.QCheckBox("Needs a rig")

        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._status.setTextFormat(QtCore.Qt.PlainText)

        self._adopt = QtWidgets.QPushButton("Use Existing Asset")
        self._create = QtWidgets.QPushButton(accept_label)
        self._buttons = QtWidgets.QDialogButtonBox()
        self._buttons.addButton(self._adopt, QtWidgets.QDialogButtonBox.ActionRole)
        self._buttons.addButton(self._create, QtWidgets.QDialogButtonBox.AcceptRole)
        self._buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self._adopt.clicked.connect(self._accept_adopt)
        self._buttons.accepted.connect(self._accept_new)
        self._buttons.rejected.connect(self.reject)

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("", self._derived)
        form.addRow("Subdirectory", self._subdirectory)
        form.addRow("", self._rigged)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._status)
        layout.addWidget(self._buttons)

        self._name.textChanged.connect(self._refresh)
        self._subdirectory.editTextChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        display_name = self._name.text().strip()
        subdirectory = self._subdirectory.currentText().strip() or None
        name = normalize_display_name(display_name)
        self._derived.setText(f"Becomes {name}" if name else "")

        self._claim, message, style = self._judge(display_name, subdirectory)
        self._status.setText(message)
        self._status.setStyleSheet(style)
        self._create.setEnabled(isinstance(self._claim, New))
        self._adopt.setVisible(isinstance(self._claim, Adopt))
        # An adopted record keeps its own tags and tasks.
        self._rigged.setEnabled(not isinstance(self._claim, Adopt))

    def _judge(
        self, display_name: str, subdirectory: str | None
    ) -> tuple[New | Adopt | None, str, str]:
        """What the typed name would claim, and what to tell the artist about it."""
        if not display_name:
            return None, "", ""
        if problem := name_problem(display_name):
            return None, problem, FAIL_STYLE
        # Judged before `classify`, which builds a path from the folder.
        if subdirectory and (
            problem := subdirectory_problem(subdirectory, self._in_use)
        ):
            return None, problem, FAIL_STYLE

        claim = classify(
            display_name, subdirectory, self._assets, self._production_root
        )
        if isinstance(claim, Occupied):
            return None, claim.reason, FAIL_STYLE
        if isinstance(claim, Adopt):
            asset = claim.asset
            return (
                claim,
                f'"{asset.display_name}" already exists in ShotGrid as a {asset.type} '
                f"at {asset.asset_path}, with no files yet. Use it, keeping its own "
                "subdirectory and tags, or choose a different name.",
                "",
            )
        if subdirectory and subdirectory not in self._in_use:
            return (
                claim,
                f'No asset uses "{subdirectory}" yet — this creates asset/{subdirectory}/.',
                WARN_STYLE,
            )
        return (
            claim,
            f"New asset at {build_asset_path(display_name, subdirectory)}.",
            OK_STYLE,
        )

    def _accept_new(self) -> None:
        # The accept button is enabled only while the claim is New.
        assert isinstance(self._claim, New)
        self.choice = dataclasses.replace(self._claim, rigged=self._rigged.isChecked())
        self.accept()

    def _accept_adopt(self) -> None:
        # The adopt button is shown only while the claim is Adopt.
        assert isinstance(self._claim, Adopt)
        self.choice = self._claim
        self.accept()


__all__ = ["NewAssetDialog", "ask_new_asset"]
