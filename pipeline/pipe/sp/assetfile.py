"""Substance Painter asset opener and project metadata helpers.

This module centralizes how Painter projects map to pipeline asset roots and
stores the chosen asset in project metadata for reuse during export.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Callable, Optional

import substance_painter as sp
from env_sg import DB_Config
from Qt import QtCore, QtWidgets
from shared.util import get_documentation_path, get_production_path, resolve_mapped_path
from substance_painter.project import NormalMapFormat, ProjectWorkflow, TangentSpace

from pipe.asset.paths import DCC_SUBSTANCE, AssetPaths, paths_for_asset
from pipe.db import DB
from pipe.glui.dialogs import (
    DialogFilteredList,
    FilteredListDialog,
    MessageDialog,
    MessageDialogCustomButtons,
)
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset, build_asset_path

log = logging.getLogger(__name__)

# Metadata context + keys (easy to grep)
PIPE_SP_METADATA_CONTEXT = "skd_asset_pipeline"
PIPE_SP_METADATA_KEY = "asset_selection"
PIPE_SP_METADATA_SCHEMA_VERSION = 1
PIPE_SP_PROJECT_TEMPLATE_NAME = "sandwich_default.spt"
PIPE_SP_PROJECT_TEMPLATE_DIR = Path("painter_assets") / "templates"
PIPE_SP_DOCS_PAGE = "Asset-Pipeline#substance-painter"


def _texture_set_name(tex_set: sp.textureset.TextureSet) -> str:
    """Return texture set name with backward compatibility across API versions."""
    name_attr = getattr(tex_set, "name", None)
    if callable(name_attr):
        return name_attr()
    if isinstance(name_attr, str):
        return name_attr
    return str(tex_set)


def _default_project_settings() -> sp.project.Settings:
    """Project settings used by the default project creator."""
    return sp.project.Settings(
        normal_map_format=NormalMapFormat.OpenGL,
        tangent_space_mode=TangentSpace.PerVertex,
        project_workflow=ProjectWorkflow.UVTile,
    )  # type: ignore[call-arg]


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


def _run_once_on_project_edition_entered(callback: Callable[[], None]) -> None:
    """Run callback once the project enters edition state."""

    def _on_project_edition_entered(_event: sp.event.Event) -> None:
        try:
            sp.event.DISPATCHER.disconnect(
                sp.event.ProjectEditionEntered, _on_project_edition_entered
            )
        except Exception:
            pass
        callback()

    sp.event.DISPATCHER.connect_strong(
        sp.event.ProjectEditionEntered, _on_project_edition_entered
    )


def _run_when_project_editable(callback: Callable[[], None]) -> None:
    """Run callback when a project is open, in edition state, and not busy."""
    if not sp.project.is_open():
        _run_once_on_project_edition_entered(
            lambda: _run_when_project_editable(callback)
        )
        return

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(lambda: _run_when_project_editable(callback))
        return

    try:
        if not sp.project.is_in_edition_state():
            _run_once_on_project_edition_entered(
                lambda: _run_when_project_editable(callback)
            )
            return
    except Exception:
        return

    callback()


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
    asset_map: dict[str, str],
    last_asset: Optional[str] = None,
    asset_id: Optional[int] = None,
    asset_path: Optional[str] = None,
    asset_subdirectory: Optional[str] = None,
    geo_variant: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PIPE_SP_METADATA_SCHEMA_VERSION,
        "dcc": DCC_SUBSTANCE,
        "asset_map": asset_map,
        "updated_at": _utc_now_iso(),
    }
    if last_asset:
        payload["last_asset"] = last_asset
    if asset_id:
        payload["asset_id"] = asset_id
    if asset_path:
        payload["asset_path"] = asset_path
    if asset_subdirectory is not None:
        payload["asset_subdirectory"] = asset_subdirectory
    if geo_variant:
        payload["geo_variant"] = geo_variant
    return payload


def store_asset_selection_metadata(
    asset_map: dict[str, str],
    *,
    last_asset: Optional[str] = None,
    asset_id: Optional[int] = None,
    asset_path: Optional[str] = None,
    asset_subdirectory: Optional[str] = None,
    geo_variant: Optional[str] = None,
) -> None:
    """Persist texture-set to asset mapping in the project metadata."""
    if not sp.project.is_open():
        return

    if sp.project.is_busy():
        _run_when_project_editable(
            lambda: store_asset_selection_metadata(
                asset_map,
                last_asset=last_asset,
                asset_id=asset_id,
                asset_path=asset_path,
                asset_subdirectory=asset_subdirectory,
                geo_variant=geo_variant,
            )
        )
        return

    try:
        if not sp.project.is_in_edition_state():
            _run_when_project_editable(
                lambda: store_asset_selection_metadata(
                    asset_map,
                    last_asset=last_asset,
                    asset_id=asset_id,
                    asset_path=asset_path,
                    asset_subdirectory=asset_subdirectory,
                    geo_variant=geo_variant,
                )
            )
            return
    except Exception:
        return

    resolved_last_asset = last_asset
    if not resolved_last_asset and asset_map:
        unique = set(asset_map.values())
        if len(unique) == 1:
            resolved_last_asset = next(iter(unique))

    payload = _build_asset_selection_payload(
        asset_map,
        last_asset=resolved_last_asset,
        asset_id=asset_id,
        asset_path=asset_path,
        asset_subdirectory=asset_subdirectory,
        geo_variant=geo_variant,
    )
    _metadata().set(PIPE_SP_METADATA_KEY, payload)


def get_active_asset_from_project(conn: DB) -> Asset | None:
    """Resolve the active asset from the current Substance project metadata.

    Returns None when no project is open or when metadata is missing.
    """
    if not sp.project.is_open():
        return None

    selection_metadata = get_asset_selection_metadata()
    if not selection_metadata:
        return _asset_from_project_path(conn)

    asset_id = selection_metadata.get("asset_id")
    if asset_id:
        try:
            return conn.get_asset_by_id(asset_id)
        except Exception as exc:
            log.warning("Failed to resolve asset by id from metadata: %s", exc)

    asset_path = selection_metadata.get("asset_path")
    if asset_path:
        try:
            return conn.get_asset_by_attr("path", asset_path)
        except Exception as exc:
            log.warning("Failed to resolve asset by path from metadata: %s", exc)

    asset_subdirectory = selection_metadata.get("asset_subdirectory")

    asset_name = selection_metadata.get("last_asset")
    if not asset_name:
        asset_map = selection_metadata.get("asset_map") or {}
        unique_assets = {name for name in asset_map.values() if name}
        if len(unique_assets) == 1:
            asset_name = next(iter(unique_assets))

    if not asset_name:
        return _asset_from_project_path(conn)

    if asset_subdirectory is not None:
        try:
            return conn.get_asset_by_attr(
                "path", build_asset_path(asset_name, asset_subdirectory)
            )
        except Exception:
            pass

    try:
        return conn.get_asset_by_display_name(asset_name)
    except Exception:
        pass

    try:
        return conn.get_asset_by_name(asset_name)
    except Exception as exc:
        log.warning("Failed to resolve asset from project metadata: %s", exc)

    return _asset_from_project_path(conn)


def _asset_from_project_path(conn: DB) -> Asset | None:
    project_path = _current_project_path()
    if not project_path:
        return None

    try:
        prod_root = get_production_path().resolve()
        project_path = project_path.resolve()
        if prod_root not in project_path.parents and project_path != prod_root:
            return None
        asset_root = project_path.parent
        rel_asset_path = asset_root.relative_to(prod_root)
    except Exception:
        return None

    rel_path_str = rel_asset_path.as_posix()
    try:
        return conn.get_asset_by_attr("path", rel_path_str)
    except Exception as exc:
        log.warning("Failed to resolve asset from project path: %s", exc)
        return None


def store_asset_metadata_for_project(
    asset: Asset, *, geo_variant: Optional[str] = None
) -> None:
    """Store a single asset selection for all current texture sets."""
    if not sp.project.is_open():
        return

    asset_display_name = asset.display_name or asset.code or asset.name
    if not asset_display_name:
        return

    asset_map = {
        _texture_set_name(texset): asset_display_name
        for texset in sp.textureset.all_texture_sets()
    }
    store_asset_selection_metadata(
        asset_map,
        last_asset=asset_display_name,
        asset_id=asset.id,
        asset_path=asset.asset_path,
        asset_subdirectory=asset.subdirectory,
        geo_variant=geo_variant,
    )
    log.info("Stored asset metadata for project: %s", asset_display_name)


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


def _geo_variants_for_asset(asset: Asset) -> list[str]:
    variants = sorted(v for v in asset.geometry_variants if v)
    if variants:
        return variants
    return ["main"]


def _project_path_for_variant(paths: AssetPaths, variant: str) -> Path:
    return paths.textures_variant_path(variant)


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
        if not asset:
            self._info_label.setText("Could not resolve the selected asset.")
            return

        paths = paths_for_asset(asset)
        project_path = _project_path_for_variant(paths, "main")
        status = "exists" if project_path.exists() else "missing"
        self._info_label.setText(
            f"Substance Painter project (main): {project_path} ({status})"
        )


class SubstanceAssetSelectDialog(QtWidgets.QDialog, DialogFilteredList):
    """Select an asset + geometry variant, then open or create a project."""

    ACTION_OPEN_EXISTING = "open_existing"
    ACTION_CREATE_PROJECT = "create_project"

    _conn: DB
    _info_label: QtWidgets.QLabel
    _action: str | None
    _asset: Asset | None
    _paths: AssetPaths | None
    _geo_variant_dropdown: QtWidgets.QComboBox

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

        variant_widget = QtWidgets.QWidget(self)
        variant_layout = QtWidgets.QHBoxLayout(variant_widget)
        variant_layout.setContentsMargins(0, 0, 0, 0)
        variant_layout.setSpacing(6)
        variant_label = QtWidgets.QLabel("Geometry Variant:")
        variant_label.setToolTip(
            "Choose which geometry variant project file to open or create."
        )
        self._geo_variant_dropdown = QtWidgets.QComboBox()
        self._geo_variant_dropdown.setToolTip(
            "Each variant uses its own Substance Painter project file."
        )
        variant_layout.addWidget(variant_label, 30)
        variant_layout.addWidget(self._geo_variant_dropdown, 70)
        layout.addWidget(variant_widget)

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
            "Tip: Select an asset and geometry variant. "
            "Each variant opens its own Substance Painter project file.<br>"
            f"For more information, see {_docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        self._list_widget.itemSelectionChanged.connect(self._on_item_selected)
        self._geo_variant_dropdown.currentTextChanged.connect(self._on_variant_changed)
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

    def get_selected_variant(self) -> str:
        return self._geo_variant_dropdown.currentText().strip() or "main"

    def _on_variant_changed(self, _text: str) -> None:
        self._update_project_info()
        self._update_state()

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._asset = None
            self._paths = None
            self._geo_variant_dropdown.clear()
            self._info_label.setText("Select an asset to see details.")
            self._update_state()
            return

        asset = self._conn.get_asset_by_name(selected)
        if not asset:
            self._asset = None
            self._paths = None
            self._geo_variant_dropdown.clear()
            self._info_label.setText("Could not resolve the selected asset.")
            self._update_state()
            return

        self._asset = asset
        self._paths = paths_for_asset(asset)
        variants = _geo_variants_for_asset(asset)
        self._geo_variant_dropdown.clear()
        self._geo_variant_dropdown.addItems(variants)
        self._geo_variant_dropdown.setCurrentText(
            "main" if "main" in variants else variants[0]
        )
        self._update_project_info()
        self._update_state()

    def _selected_project_path(self) -> Path | None:
        if not self._paths:
            return None
        return _project_path_for_variant(self._paths, self.get_selected_variant())

    def _update_project_info(self) -> None:
        project_path = self._selected_project_path()
        if not project_path:
            return
        variant = self.get_selected_variant()
        status = "exists" if project_path.exists() else "missing"
        self._info_label.setText(
            f"Geometry variant: {variant}\n"
            f"Substance Painter project: {project_path} ({status})"
        )

    def _update_state(self) -> None:
        has_asset = bool(self._asset)
        self._geo_variant_dropdown.setEnabled(has_asset)
        project_path = self._selected_project_path()
        project_exists = bool(project_path and project_path.exists())
        self._open_existing_btn.setEnabled(project_exists)
        self._create_project_btn.setEnabled(has_asset)


class SubstanceAssetCreateModeDialog(QtWidgets.QDialog):
    """Choose how to create the Substance Painter project for an asset."""

    ACTION_CREATE_DEFAULT = "create_default"
    ACTION_USE_CURRENT = "use_current"

    _action: str | None

    def __init__(
        self, parent: QtWidgets.QWidget | None, asset: Asset, geo_variant: str
    ) -> None:
        super().__init__(parent)
        self._action = None
        variant_name = geo_variant.strip() or "main"

        self.setParent(parent)
        self.setWindowTitle("Create Asset Project")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(480, 320)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        asset_label = asset.display_name or asset.name or "Asset"
        title = QtWidgets.QLabel(
            f"Create new Substance Painter project for {asset_label} ({variant_name})"
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
            'Tip: use "Create Default Project" unless you have talked with your team lead. '
            "The project will be saved to the selected geometry variant file.<br>"
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
    _geo_variant: str
    _mesh_status_label: QtWidgets.QLabel
    _resolved_mesh_path: Path | None

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        asset: Asset,
        paths: AssetPaths,
        geo_variant: str,
    ) -> None:
        super().__init__(parent)
        self._asset = asset
        self._paths = paths
        self._geo_variant = geo_variant.strip() or "main"
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
        self._geo_variant_radio = QtWidgets.QRadioButton("Published Variant Mesh")
        self._geo_variant_radio.setChecked(True)
        self._geo_variant_radio.setToolTip(
            "Use a published geometry variant from the asset's _src folder."
        )
        variant_value = QtWidgets.QLabel(self._geo_variant)
        variant_value.setToolTip("Selected geometry variant for this project file.")
        variant_value.setTextFormat(QtCore.Qt.PlainText)
        variant_row.addWidget(self._geo_variant_radio, 30)
        variant_row.addWidget(variant_value, 70)
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
            "Tip: The selected geometry variant is locked for this project file. "
            "Use Custom Mesh to browse for any file.<br>"
            f"For more information, see {_docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        self._custom_mesh_field.textChanged.connect(self._update_mesh_status)
        self._geo_variant_radio.toggled.connect(self._update_create_mode)
        self._custom_mesh_radio.toggled.connect(self._update_create_mode)
        self._custom_mesh_browse.clicked.connect(self._browse_custom_mesh)
        self._create_default_btn.clicked.connect(self.accept)
        self._update_create_mode()

    def use_custom_mesh(self) -> bool:
        return self._custom_mesh_radio.isChecked()

    def get_selected_variant(self) -> str:
        return self._geo_variant

    def get_custom_mesh_path(self) -> Path | None:
        text = self._custom_mesh_field.text().strip()
        if not text:
            return None
        return Path(text).expanduser()

    def get_resolved_mesh_path(self) -> Path | None:
        return self._resolved_mesh_path

    def _update_create_mode(self) -> None:
        use_variant = self._geo_variant_radio.isChecked()
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
        variant = self._geo_variant
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


def _store_asset_metadata_when_ready(
    asset: Asset, *, geo_variant: Optional[str] = None
) -> None:
    _run_when_project_editable(
        lambda: store_asset_metadata_for_project(asset, geo_variant=geo_variant)
    )


def _open_existing_project(path: Path, parent: QtWidgets.QWidget | None) -> bool:
    resolved_path = resolve_mapped_path(path)
    try:
        sp.project.open(str(resolved_path))
    except Exception:
        log.exception("Failed to open Substance Painter project: %s", resolved_path)
        MessageDialog(
            parent,
            f"Failed to open the Substance Painter project:\n{resolved_path}",
            "Open Project Failed",
        ).exec_()
        return False
    return True


def _save_current_project_as(path: Path, parent: QtWidgets.QWidget | None) -> bool:
    resolved_path = resolve_mapped_path(path)
    try:
        sp.project.save_as(str(resolved_path))
    except Exception:
        log.exception("Failed to save Substance Painter project as: %s", resolved_path)
        MessageDialog(
            parent,
            f"Failed to save the Substance Painter project:\n{resolved_path}",
            "Save Failed",
        ).exec_()
        return False
    return True


def _close_current_project(
    parent: QtWidgets.QWidget | None, *, action_context: str
) -> bool:
    try:
        sp.project.close()
    except Exception:
        log.exception(
            "Failed to close Substance Painter project before %s.", action_context
        )
        MessageDialog(
            parent,
            "Failed to close the currently opened project. "
            "Resolve any pending project issues and try again.",
            "Close Project Failed",
        ).exec_()
        return False
    return True


def _open_existing_project_for_asset(
    asset: Asset, project_path: Path, *, geo_variant: str
) -> None:
    parent = get_main_qt_window()
    if not project_path.exists():
        MessageDialog(
            parent,
            "No Substance Painter project exists yet. Use Save Current As or Create Default.",
            "Missing Substance Painter Project",
        ).exec_()
        log.warning("Substance project missing at %s", project_path)
        return

    current_path = _current_project_path()
    if current_path and current_path.resolve() == project_path.resolve():
        if sp.project.needs_saving():
            if not _save_current_project_as(project_path, parent):
                return
        _store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        if not _close_current_project(
            parent, action_context="opening another asset project"
        ):
            return

    if not _open_existing_project(project_path, parent):
        return
    _store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
    log.info(
        "Opened Substance project for asset %s (variant=%s)",
        asset.display_name or asset.name,
        geo_variant,
    )


def _save_current_project_as_asset(
    asset: Asset, project_path: Path, *, geo_variant: str
) -> None:
    parent = get_main_qt_window()
    if not sp.project.is_open():
        MessageDialog(
            parent,
            "No project is currently open. Open or create a project before saving.",
            "No Project Open",
        ).exec_()
        log.warning("Save current project requested with no project open.")
        return

    current_path = _current_project_path()
    if current_path and current_path.resolve() == project_path.resolve():
        _store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
        return

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    if not _save_current_project_as(project_path, parent):
        return
    _store_asset_metadata_when_ready(asset, geo_variant=geo_variant)
    log.info("Saved Substance project to %s (variant=%s)", project_path, geo_variant)


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
    log.info(
        "Creating default Substance project for %s (variant=%s)",
        asset.display_name or asset.name,
        variant,
    )

    mesh_path, variant_path, fallback_path = _resolve_default_mesh_paths(
        paths,
        use_custom_mesh=use_custom_mesh,
        custom_mesh_path=custom_mesh_path,
        variant=variant,
    )

    if not mesh_path or not mesh_path.exists():
        if use_custom_mesh:
            message = (
                "The selected custom mesh is missing. Choose a valid mesh to proceed."
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
        if not _close_current_project(
            parent, action_context="creating a default asset project"
        ):
            return

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
            settings=_default_project_settings(),
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
        if not _save_current_project_as(resolved_project_path, parent):
            return
        _store_asset_metadata_when_ready(asset, geo_variant=variant)

    _run_when_project_editable(finalize_save)
    log.info("Created Substance project at %s", project_path)


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
    geo_variant = select_dialog.get_selected_variant()
    if not action or not asset:
        return
    log.info(
        "Open Asset: selected %s (%s, variant=%s)",
        asset.display_name or asset.name,
        action,
        geo_variant,
    )
    paths = paths_for_asset(asset)
    project_path = _project_path_for_variant(paths, geo_variant)

    if action == SubstanceAssetSelectDialog.ACTION_OPEN_EXISTING:
        _open_existing_project_for_asset(asset, project_path, geo_variant=geo_variant)
        return

    create_dialog = SubstanceAssetCreateModeDialog(parent, asset, geo_variant)
    if not create_dialog.exec_():
        return

    create_action = create_dialog.get_selected_action()
    if not create_action:
        return

    if create_action == SubstanceAssetCreateModeDialog.ACTION_USE_CURRENT:
        _save_current_project_as_asset(asset, project_path, geo_variant=geo_variant)
        return

    if create_action == SubstanceAssetCreateModeDialog.ACTION_CREATE_DEFAULT:
        default_dialog = SubstanceAssetDefaultProjectDialog(
            parent, asset, paths, geo_variant
        )
        if not default_dialog.exec_():
            return
        _create_default_project_for_asset(
            asset,
            project_path,
            use_custom_mesh=default_dialog.use_custom_mesh(),
            variant=geo_variant,
            custom_mesh_path=default_dialog.get_custom_mesh_path(),
        )


__all__ = [
    "PIPE_SP_METADATA_CONTEXT",
    "PIPE_SP_METADATA_KEY",
    "PIPE_SP_METADATA_SCHEMA_VERSION",
    "get_active_asset_from_project",
    "get_asset_selection_metadata",
    "store_asset_metadata_for_project",
    "store_asset_selection_metadata",
    "launch_open_asset_textures",
]
