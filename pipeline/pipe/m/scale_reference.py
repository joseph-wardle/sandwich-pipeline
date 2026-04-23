from __future__ import annotations

import logging

import maya.cmds as mc
from env_sg import DB_Config
from Qt import QtWidgets
from Qt.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pipe.asset.paths import paths_for_asset
from pipe.db import DB
from pipe.glui.dialogs import ButtonPair, MessageDialog
from pipe.m.command import maya_command
from pipe.m.local import get_main_qt_window
from pipe.m.optionvar import StringOptionVar

log = logging.getLogger(__name__)

_ASSET_TYPES = ("Character",)
_NODE_PREFIX = "scaleRef"
_MAYA_SCALE = 100.0

_dialog: ScaleReferenceDialog | None = None


@maya_command(
    name="scale_reference",
    label="Scale Reference",
    category="modeling",
    icon="yoon.svg",
)
def show_scale_reference_dialog() -> ScaleReferenceDialog:
    """Open the scale reference dialog, closing any existing instance first."""
    global _dialog
    parent = get_main_qt_window()

    if _dialog is not None:
        try:
            _dialog.close()
            _dialog.deleteLater()
        except Exception:
            pass

    _dialog = ScaleReferenceDialog(parent)
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog


class ScaleReference:
    """Backward-compatible shelf wrapper."""

    def __init__(self) -> None:
        self.dialog = show_scale_reference_dialog()


def _import_usd_scale_reference(name: str, usd_path, scale: float) -> None:
    """Create a mayaUsdProxyShape scale reference node in the current scene."""
    if not mc.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
        mc.loadPlugin("mayaUsdPlugin")

    if mc.objExists(name):
        mc.delete(name)

    transform = mc.createNode("transform", name=name)
    shape = mc.createNode("mayaUsdProxyShape", name=f"{name}Shape", parent=transform)
    mc.setAttr(f"{shape}.filePath", str(usd_path), type="string")
    mc.setAttr(f"{transform}.scale", scale, scale, scale)  # type: ignore
    mc.select(transform)


class ScaleReferenceDialog(ButtonPair, QtWidgets.QDialog):
    """Dialog for importing a character as a USD scale reference into Maya."""

    _last_asset_var = StringOptionVar("scaleReference.lastAsset", "")

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scale Reference")
        self._asset_names: list[str] = []
        self._asset_display_names: list[str] = []
        self._setup_ui()
        self._load_characters()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Character"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(180)
        row.addWidget(self._combo)
        layout.addLayout(row)

        self._status = QLabel()
        self._status.setStyleSheet("color: #b00020;")
        self._status.setVisible(False)
        layout.addWidget(self._status)

        remove_button = QPushButton("Remove All Scale Refs")
        remove_button.clicked.connect(self._remove_all_refs)
        layout.addWidget(remove_button)

        self._init_buttons(has_cancel_button=True, ok_name="Import")
        self.buttons.accepted.connect(self._do_import)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _load_characters(self) -> None:
        try:
            conn = DB.Get(DB_Config)
            # Fetch unsorted and zip together so ordering is guaranteed to match.
            # See get_asset_name_list_by_type — it derives names from display names
            # in the same iteration order when sorted=False.
            raw_display = conn.get_asset_display_name_list_by_type(list(_ASSET_TYPES))
            raw_names = conn.get_asset_name_list_by_type(list(_ASSET_TYPES))
        except Exception:
            log.exception("Could not load character list from ShotGrid.")
            self._show_status("Could not load characters from ShotGrid.")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return

        pairs = sorted(zip(raw_display, raw_names), key=lambda p: p[0])
        self._asset_display_names = [p[0] for p in pairs]
        self._asset_names = [p[1] for p in pairs]

        self._combo.clear()
        for display_name in self._asset_display_names:
            self._combo.addItem(display_name)

        last = self._last_asset_var.value
        if last and last in self._asset_names:
            self._combo.setCurrentIndex(self._asset_names.index(last))

    def _show_status(self, message: str) -> None:
        self._status.setText(message)
        self._status.setVisible(True)

    def _hide_status(self) -> None:
        self._status.setVisible(False)

    def _selected_asset_name(self) -> str | None:
        idx = self._combo.currentIndex()
        if idx < 0 or idx >= len(self._asset_names):
            return None
        return self._asset_names[idx]

    def _do_import(self) -> None:
        asset_name = self._selected_asset_name()
        if not asset_name:
            return

        try:
            conn = DB.Get(DB_Config)
            asset = conn.get_asset_by_name(asset_name)
        except Exception:
            log.exception("Could not resolve asset '%s' from ShotGrid.", asset_name)
            MessageDialog(self, f"Could not resolve asset '{asset_name}'.").exec_()
            return

        asset_paths = paths_for_asset(asset)
        usd_path = asset_paths.publish_dir / f"{asset.name}.usd"
        if not usd_path.exists():
            MessageDialog(
                self,
                f"Published USD not found for '{asset_name}':\n{usd_path}",
                "Scale Reference",
            ).exec_()
            return

        self._last_asset_var.value = asset_name

        node_name = f"{_NODE_PREFIX}_{asset_name}"
        try:
            _import_usd_scale_reference(node_name, usd_path, _MAYA_SCALE)
        except Exception as exc:
            log.exception("Scale reference import failed for '%s'.", asset_name)
            MessageDialog(self, f"Import failed:\n{exc}", "Scale Reference").exec_()
            return

        self.accept()

    def _remove_all_refs(self) -> None:
        prefix = f"{_NODE_PREFIX}_"
        nodes: list[str] = [
            n for n in (mc.ls(transforms=True) or []) if n.startswith(prefix)
        ]
        if nodes:
            mc.delete(nodes)  # type: ignore
        self._hide_status()


__all__ = ["ScaleReference", "show_scale_reference_dialog"]
