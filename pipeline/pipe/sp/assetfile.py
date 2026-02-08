"""Substance Painter asset opener and project metadata helpers.

This module centralizes how Painter projects map to pipeline asset roots and
stores the chosen asset in project metadata for reuse during export.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Optional

import substance_painter as sp
from env_sg import DB_Config
from Qt import QtCore, QtWidgets
from shared.util import get_documentation_path, get_production_path, resolve_mapped_path

from pipe.asset.paths import DCC_SUBSTANCE, AssetPaths, paths_for_asset
from pipe.db import DB
from pipe.glui.dialogs import (
    DialogFilteredList,
    FilteredListDialog,
    MessageDialog,
    MessageDialogCustomButtons,
)
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset

log = logging.getLogger(__name__)

# Metadata context + keys (easy to grep)
PIPE_SP_METADATA_CONTEXT = "skd_asset_pipeline"
PIPE_SP_METADATA_KEY = "asset_selection"
PIPE_SP_METADATA_SCHEMA_VERSION = 1
PIPE_SP_PROJECT_TEMPLATE_NAME = "sandwich_default.spt"
PIPE_SP_PROJECT_TEMPLATE_DIR = Path("painter_assets") / "templates"
PIPE_SP_DOCS_PAGE = "Asset-Pipeline#substance-painter"


def _substance_docs_url() -> str:
    return get_documentation_path(PIPE_SP_DOCS_PAGE)


def _docs_link_html() -> str:
    url = _substance_docs_url()
    if "://" not in url:
        url = QtCore.QUrl.fromLocalFile(url).toString()
    return f'<a href="{url}">the documentation</a>'


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _metadata() -> sp.project.Metadata:
    return sp.project.Metadata(PIPE_SP_METADATA_CONTEXT)


def _safe_get_metadata() -> dict[str, Any]:
    if not sp.project.is_open():
        return {}
    try:
        payload = _metadata().get(PIPE_SP_METADATA_KEY)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_asset_selection_metadata() -> dict[str, Any]:
    """Return the stored asset selection metadata, if any."""
    return _safe_get_metadata()


def _build_asset_selection_payload(
    asset_map: dict[str, str], last_asset: Optional[str] = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PIPE_SP_METADATA_SCHEMA_VERSION,
        "dcc": DCC_SUBSTANCE,
        "asset_map": asset_map,
        "updated_at": _utc_now_iso(),
    }
    if last_asset:
        payload["last_asset"] = last_asset
    return payload


def store_asset_selection_metadata(asset_map: dict[str, str]) -> None:
    """Persist texture-set to asset mapping in the project metadata."""
    if not sp.project.is_open():
        return
    if sp.project.is_busy():
        sp.project.execute_when_not_busy(
            lambda: store_asset_selection_metadata(asset_map)
        )
        return

    last_asset = None
    if asset_map:
        unique = set(asset_map.values())
        if len(unique) == 1:
            last_asset = next(iter(unique))

    payload = _build_asset_selection_payload(asset_map, last_asset=last_asset)
    _metadata().set(PIPE_SP_METADATA_KEY, payload)


def store_asset_metadata_for_project(asset: Asset) -> None:
    """Store a single asset selection for all current texture sets."""
    if not sp.project.is_open():
        return

    asset_name = asset.name or asset.disp_name
    if not asset_name:
        return

    asset_map = {
        texset.name(): asset_name for texset in sp.textureset.all_texture_sets()
    }
    store_asset_selection_metadata(asset_map)


def _resolve_default_mesh_paths(
    paths: AssetPaths,
    *,
    use_custom_mesh: bool,
    custom_mesh_path: Path | None,
    variant: str,
) -> tuple[Path | None, Path | None, Path | None]:
    """Return (selected_path, variant_path, fallback_path) for default projects."""
    if use_custom_mesh:
        return custom_mesh_path, None, None

    variant_name = variant.strip() or "main"
    variant_path = paths.publish_source_variant_usd(variant_name)
    fallback_path = paths.publish_source_model_usd if variant_name == "main" else None

    if variant_path.exists():
        return variant_path, variant_path, fallback_path
    if fallback_path and fallback_path.exists():
        return fallback_path, variant_path, fallback_path
    return variant_path, variant_path, fallback_path


def _project_template_path() -> Path:
    """Return the expected template path in the production painter assets."""
    return (
        get_production_path()
        / PIPE_SP_PROJECT_TEMPLATE_DIR
        / PIPE_SP_PROJECT_TEMPLATE_NAME
    )


class SubstanceAssetDialog(FilteredListDialog):
    """Select an asset and preview the canonical textures path."""

    _conn: DB
    _info_label: QtWidgets.QLabel

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: DB
    ) -> None:
        super().__init__(
            parent,
            items,
            "Open Asset Textures",
            "Select the asset to open its Substance Painter project.",
            accept_button_name="Open",
        )
        self._conn = conn

        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        info_layout.addWidget(self._info_label)

        self._layout.insertWidget(1, info_widget)

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._info_label.setText("Select an asset to see details.")
            return

        asset = self._conn.get_asset_by_name(selected)
        if not asset or not asset.path:
            self._info_label.setText("Asset path not set in ShotGrid.")
            return

        paths = paths_for_asset(asset)
        status = "exists" if paths.textures_path.exists() else "missing"
        self._info_label.setText(
            f"Substance Painter project: {paths.textures_path} ({status})"
        )


class SubstanceAssetSelectDialog(QtWidgets.QDialog, DialogFilteredList):
    """Select an asset and choose to open or create its Substance Painter project."""

    ACTION_OPEN_EXISTING = "open_existing"
    ACTION_CREATE_PROJECT = "create_project"

    _conn: DB
    _info_label: QtWidgets.QLabel
    _action: str | None
    _asset: Asset | None
    _paths: AssetPaths | None

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: DB
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._action = None
        self._asset = None
        self._paths = None

        self._init_filtered_list(
            items,
            list_label="Select an asset to open or create its Substance Painter project.",
            include_filter_field=True,
        )

        self.setParent(parent)
        self.setWindowTitle("Open Asset")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(560, 640)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.filtered_list)

        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        info_layout.addWidget(self._info_label)
        layout.addWidget(info_widget)

        buttons_layout = QtWidgets.QHBoxLayout()
        self._open_existing_btn = QtWidgets.QPushButton("Open Asset Project")
        self._create_project_btn = QtWidgets.QPushButton("Create Asset Project")
        self._open_existing_btn.setToolTip(
            "Open the existing Substance Painter project for the selected asset."
        )
        self._create_project_btn.setToolTip(
            "Create a new Substance Painter Project for the selected asset."
        )
        buttons_layout.addWidget(self._open_existing_btn)
        buttons_layout.addWidget(self._create_project_btn)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)

        footer = QtWidgets.QLabel(
            "Tip: Select an asset, then open its Substance Painter project or create a new one.<br>"
            f"For more information, see {_docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        self._list_widget.itemSelectionChanged.connect(self._on_item_selected)
        self._open_existing_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_OPEN_EXISTING)
        )
        self._create_project_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_CREATE_PROJECT)
        )
        self._update_state()

    def _set_action_and_accept(self, action: str) -> None:
        self._action = action
        self.accept()

    def get_selected_action(self) -> str | None:
        return self._action

    def get_selected_asset(self) -> Asset | None:
        return self._asset

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._asset = None
            self._paths = None
            self._info_label.setText("Select an asset to see details.")
            self._update_state()
            return

        asset = self._conn.get_asset_by_name(selected)
        if not asset or not asset.path:
            self._asset = None
            self._paths = None
            self._info_label.setText("Asset path not set in ShotGrid.")
            self._update_state()
            return

        self._asset = asset
        self._paths = paths_for_asset(asset)
        project_exists = self._paths.textures_path.exists()
        status = "exists" if project_exists else "missing"
        self._info_label.setText(
            f"Substance Painter project: {self._paths.textures_path} ({status})"
        )
        self._update_state()

    def _update_state(self) -> None:
        has_asset = bool(self._asset)
        project_exists = False
        if self._paths:
            project_exists = self._paths.textures_path.exists()
        self._open_existing_btn.setEnabled(project_exists)
        self._create_project_btn.setEnabled(has_asset)


class SubstanceAssetCreateModeDialog(QtWidgets.QDialog):
    """Choose how to create the Substance Painter project for an asset."""

    ACTION_CREATE_DEFAULT = "create_default"
    ACTION_USE_CURRENT = "use_current"

    _action: str | None

    def __init__(
        self, parent: QtWidgets.QWidget | None, asset: Asset, paths: AssetPaths
    ) -> None:
        super().__init__(parent)
        self._action = None

        self.setParent(parent)
        self.setWindowTitle("Create Asset Project")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(480, 320)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        asset_label = asset.name or asset.disp_name or "Asset"
        title = QtWidgets.QLabel(
            f"Create new Substance Painter project for {asset_label}"
        )
        title.setTextFormat(QtCore.Qt.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)

        layout.addStretch(1)

        buttons_layout = QtWidgets.QHBoxLayout()
        self._create_default_btn = QtWidgets.QPushButton("Create Default Project")
        self._use_current_btn = QtWidgets.QPushButton("Use Currently Open Project")
        self._create_default_btn.setToolTip(
            "Create a new project using the published mesh."
        )
        self._use_current_btn.setToolTip(
            "Save the currently open project to this asset."
        )
        buttons_layout.addWidget(self._create_default_btn)
        buttons_layout.addWidget(self._use_current_btn)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)

        footer = QtWidgets.QLabel(
            'Tip: use "Create Default Project" unless you have talked with your team lead.<br>'
            f"For more information, see {_docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        self._create_default_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_CREATE_DEFAULT)
        )
        self._use_current_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_USE_CURRENT)
        )
        self._use_current_btn.setEnabled(sp.project.is_open())

    def _set_action_and_accept(self, action: str) -> None:
        self._action = action
        self.accept()

    def get_selected_action(self) -> str | None:
        return self._action


class SubstanceAssetDefaultProjectDialog(QtWidgets.QDialog):
    """Pick a geometry source to create a default Substance Painter project."""

    _asset: Asset
    _paths: AssetPaths
    _mesh_status_label: QtWidgets.QLabel
    _resolved_mesh_path: Path | None

    def __init__(
        self, parent: QtWidgets.QWidget | None, asset: Asset, paths: AssetPaths
    ) -> None:
        super().__init__(parent)
        self._asset = asset
        self._paths = paths
        self._resolved_mesh_path = None

        self.setParent(parent)
        self.setWindowTitle("Create Default Project")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(560, 360)

        layout = QtWidgets.QVBoxLayout(self)

        info_label = QtWidgets.QLabel(
            "Choose the mesh source for the default Substance Painter project."
        )
        info_label.setWordWrap(True)
        info_label.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(info_label)

        variant_row = QtWidgets.QHBoxLayout()
        self._geo_variant_radio = QtWidgets.QRadioButton("Geometry Variant")
        self._geo_variant_radio.setChecked(True)
        self._geo_variant_dropdown = QtWidgets.QComboBox()
        self._geo_variant_radio.setToolTip(
            "Use a published geometry variant from the asset's _src folder."
        )
        self._geo_variant_dropdown.setToolTip(
            "Select the geometry variant to use for this assets Substance Project."
        )
        variant_row.addWidget(self._geo_variant_radio, 30)
        variant_row.addWidget(self._geo_variant_dropdown, 70)
        layout.addLayout(variant_row)

        custom_row = QtWidgets.QHBoxLayout()
        self._custom_mesh_radio = QtWidgets.QRadioButton("Custom Mesh")
        self._custom_mesh_field = QtWidgets.QLineEdit()
        self._custom_mesh_field.setPlaceholderText("Select a custom mesh...")
        self._custom_mesh_browse = QtWidgets.QPushButton("Browse...")
        self._custom_mesh_radio.setToolTip(
            "Use a custom mesh file instead of the published variant."
        )
        self._custom_mesh_field.setToolTip("Path to a custom mesh file.")
        self._custom_mesh_browse.setToolTip("Browse for a custom mesh file.")
        custom_row.addWidget(self._custom_mesh_radio, 30)
        custom_row.addWidget(self._custom_mesh_field, 55)
        custom_row.addWidget(self._custom_mesh_browse, 15)
        layout.addLayout(custom_row)

        self._mesh_status_label = QtWidgets.QLabel("Mesh source: --")
        self._mesh_status_label.setWordWrap(True)
        self._mesh_status_label.setTextFormat(QtCore.Qt.PlainText)
        self._mesh_status_label.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._mesh_status_label)

        buttons_layout = QtWidgets.QHBoxLayout()
        self._create_default_btn = QtWidgets.QPushButton("Create Default Project")
        self._create_default_btn.setToolTip(
            "Create a new project using the selected mesh source."
        )
        buttons_layout.addWidget(self._create_default_btn)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)

        footer = QtWidgets.QLabel(
            "Tip: Geometry variants come from publish/_src. "
            "Use Custom Mesh to browse for any file.<br>"
            f"For more information, see {_docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        self._geo_variant_dropdown.currentTextChanged.connect(self._update_mesh_status)
        self._custom_mesh_field.textChanged.connect(self._update_mesh_status)
        self._geo_variant_radio.toggled.connect(self._update_create_mode)
        self._custom_mesh_radio.toggled.connect(self._update_create_mode)
        self._custom_mesh_browse.clicked.connect(self._browse_custom_mesh)
        self._create_default_btn.clicked.connect(self.accept)
        self._populate_geo_variants()
        self._update_create_mode()

    def use_custom_mesh(self) -> bool:
        return self._custom_mesh_radio.isChecked()

    def get_selected_variant(self) -> str:
        return self._geo_variant_dropdown.currentText().strip()

    def get_custom_mesh_path(self) -> Path | None:
        text = self._custom_mesh_field.text().strip()
        if not text:
            return None
        return Path(text).expanduser()

    def get_resolved_mesh_path(self) -> Path | None:
        return self._resolved_mesh_path

    def _populate_geo_variants(self) -> None:
        variants = set()
        if hasattr(self._asset, "geometry_variants"):
            variants.update(self._asset.geometry_variants)
        variants.add("main")
        ordered = sorted(v for v in variants if v)
        self._geo_variant_dropdown.clear()
        self._geo_variant_dropdown.addItems(ordered)
        if "main" in ordered:
            self._geo_variant_dropdown.setCurrentText("main")

    def _update_create_mode(self) -> None:
        use_variant = self._geo_variant_radio.isChecked()
        self._geo_variant_dropdown.setEnabled(use_variant)
        self._custom_mesh_field.setEnabled(not use_variant)
        self._custom_mesh_browse.setEnabled(not use_variant)
        self._update_mesh_status()

    def _browse_custom_mesh(self) -> None:
        base_dir = str(self._paths.publish_source_dir)
        selection, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Mesh File",
            base_dir,
            "Mesh Files (*.usd *.usda *.usdc *.obj *.fbx *.abc);;All Files (*)",
        )
        if selection:
            resolved = resolve_mapped_path(Path(selection))
            self._custom_mesh_field.setText(str(resolved))

    def _update_mesh_status(self) -> None:
        self._resolved_mesh_path = None

        use_custom = self._custom_mesh_radio.isChecked()
        variant = self.get_selected_variant()
        custom_mesh = self.get_custom_mesh_path()

        resolved, variant_path, fallback_path = _resolve_default_mesh_paths(
            self._paths,
            use_custom_mesh=use_custom,
            custom_mesh_path=custom_mesh,
            variant=variant,
        )

        self._resolved_mesh_path = resolved

        if not resolved:
            self._mesh_status_label.setText("Mesh source: --")
        elif resolved.exists():
            if fallback_path and resolved == fallback_path and variant_path:
                self._mesh_status_label.setText(
                    f"Mesh source: {resolved} (fallback from {variant_path.name})"
                )
            else:
                self._mesh_status_label.setText(f"Mesh source: {resolved} (exists)")
        else:
            if fallback_path and variant_path and fallback_path != resolved:
                self._mesh_status_label.setText(
                    "Mesh source: {} (missing; fallback {} missing)".format(
                        variant_path, fallback_path
                    )
                )
            else:
                self._mesh_status_label.setText(f"Mesh source: {resolved} (missing)")

        mesh_ready = resolved is not None and resolved.exists()
        self._create_default_btn.setEnabled(mesh_ready)


def _confirm_discard_unsaved(parent: QtWidgets.QWidget | None) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        "The current project has unsaved changes. Continue and discard them?",
        "Unsaved Changes",
        has_cancel_button=True,
        ok_name="Continue",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _confirm_save_as(parent: QtWidgets.QWidget | None, path: Path) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        f"No project exists at {path}. Save the current project there?",
        "Create Substance Painter Project",
        has_cancel_button=True,
        ok_name="Save As",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _confirm_overwrite_project(parent: QtWidgets.QWidget | None, path: Path) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        f"A Substance Painter project already exists at {path}. Overwrite it?",
        "Overwrite Substance Painter Project",
        has_cancel_button=True,
        ok_name="Overwrite",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _current_project_path() -> Path | None:
    try:
        current_path = sp.project.file_path()
    except Exception:
        return None
    if not current_path:
        return None
    return Path(current_path)


def _store_asset_metadata_when_ready(asset: Asset) -> None:
    if not sp.project.is_open():
        return
    sp.project.execute_when_not_busy(lambda: store_asset_metadata_for_project(asset))


def _open_existing_project(path: Path) -> None:
    sp.project.open(str(path))


def _save_current_project_as(path: Path) -> None:
    sp.project.save_as(str(path))


def _open_existing_project_for_asset(asset: Asset, project_path: Path) -> None:
    parent = get_main_qt_window()
    if not project_path.exists():
        MessageDialog(
            parent,
            "No Substance Painter project exists yet. Use Save Current As or Create Default.",
            "Missing Substance Painter Project",
        ).exec_()
        return

    current_path = _current_project_path()
    if current_path and current_path.resolve() == project_path.resolve():
        if sp.project.needs_saving():
            _save_current_project_as(project_path)
        _store_asset_metadata_when_ready(asset)
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        sp.project.close()

    _open_existing_project(project_path)
    _store_asset_metadata_when_ready(asset)


def _save_current_project_as_asset(asset: Asset, project_path: Path) -> None:
    parent = get_main_qt_window()
    if not sp.project.is_open():
        MessageDialog(
            parent,
            "No project is currently open. Open or create a project before saving.",
            "No Project Open",
        ).exec_()
        return

    current_path = _current_project_path()
    if current_path and current_path.resolve() == project_path.resolve():
        _store_asset_metadata_when_ready(asset)
        return

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    _save_current_project_as(project_path)
    _store_asset_metadata_when_ready(asset)


def _create_default_project_for_asset(
    asset: Asset,
    project_path: Path,
    *,
    use_custom_mesh: bool,
    variant: str,
    custom_mesh_path: Path | None,
) -> None:
    parent = get_main_qt_window()
    paths = paths_for_asset(asset)

    mesh_path, variant_path, fallback_path = _resolve_default_mesh_paths(
        paths,
        use_custom_mesh=use_custom_mesh,
        custom_mesh_path=custom_mesh_path,
        variant=variant,
    )

    if not mesh_path or not mesh_path.exists():
        if use_custom_mesh:
            message = (
                "The selected custom mesh is missing. "
                "Choose a valid mesh to proceed."
            )
        elif fallback_path and variant_path and variant_path != fallback_path:
            message = (
                "No published mesh was found for the selected variant.\n"
                f"Expected: {variant_path}\nFallback: {fallback_path}"
            )
        else:
            message = (
                "No published mesh was found for the selected variant.\n"
                f"Expected: {variant_path}"
            )
        MessageDialog(parent, message, "Missing Mesh Source").exec_()
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        sp.project.close()

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    template_path = _project_template_path()
    if not template_path.exists():
        MessageDialog(
            parent,
            "The default Painter template is missing:\n"
            f"{template_path}\n"
            "Contact production to restore the template.",
            "Missing Template",
        ).exec_()
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_mesh = resolve_mapped_path(mesh_path)
    resolved_template = resolve_mapped_path(template_path)
    try:
        sp.project.create(
            mesh_file_path=str(resolved_mesh),
            template_file_path=str(resolved_template),
        )
    except Exception:
        log.exception("Failed to create Painter project from template.")
        MessageDialog(
            parent,
            "Failed to create the project from the default template. "
            "Check the template and mesh file, then try again.",
            "Create Default",
        ).exec_()
        return

    resolved_project_path = resolve_mapped_path(project_path)

    def finalize_save() -> None:
        _save_current_project_as(resolved_project_path)
        _store_asset_metadata_when_ready(asset)

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(finalize_save)
    else:
        finalize_save()


def launch_open_asset_textures() -> None:
    """Open or create the Substance Painter project for a selected asset."""

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(launch_open_asset_textures)
        return

    conn = DB.Get(DB_Config)
    asset_names = conn.get_asset_name_list(sorted=True)
    parent = get_main_qt_window()

    select_dialog = SubstanceAssetSelectDialog(parent, asset_names, conn)
    if not select_dialog.exec_():
        return

    asset = select_dialog.get_selected_asset()
    action = select_dialog.get_selected_action()
    if not action or not asset:
        return
    if not asset.path:
        MessageDialog(
            parent,
            "The selected asset does not have a valid path in ShotGrid.",
            "Missing Asset Path",
        ).exec_()
        return

    paths = paths_for_asset(asset)

    if action == SubstanceAssetSelectDialog.ACTION_OPEN_EXISTING:
        _open_existing_project_for_asset(asset, paths.textures_path)
        return

    create_dialog = SubstanceAssetCreateModeDialog(parent, asset, paths)
    if not create_dialog.exec_():
        return

    create_action = create_dialog.get_selected_action()
    if not create_action:
        return

    if create_action == SubstanceAssetCreateModeDialog.ACTION_USE_CURRENT:
        _save_current_project_as_asset(asset, paths.textures_path)
        return

    if create_action == SubstanceAssetCreateModeDialog.ACTION_CREATE_DEFAULT:
        default_dialog = SubstanceAssetDefaultProjectDialog(parent, asset, paths)
        if not default_dialog.exec_():
            return
        _create_default_project_for_asset(
            asset,
            paths.textures_path,
            use_custom_mesh=default_dialog.use_custom_mesh(),
            variant=default_dialog.get_selected_variant(),
            custom_mesh_path=default_dialog.get_custom_mesh_path(),
        )


__all__ = [
    "PIPE_SP_METADATA_CONTEXT",
    "PIPE_SP_METADATA_KEY",
    "PIPE_SP_METADATA_SCHEMA_VERSION",
    "get_asset_selection_metadata",
    "store_asset_metadata_for_project",
    "store_asset_selection_metadata",
    "launch_open_asset_textures",
]
