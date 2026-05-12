"""Asset selection and project creation dialogs for Substance Painter.

Provides the dialog workflow for choosing a pipeline asset, picking a
geometry variant, and configuring how a new Substance Painter project
should be created (from a template + mesh, or by saving the current project).

Dialog flow
-----------
1. SubstanceAssetSelectDialog — pick an asset and variant, then open or create
2. SubstanceAssetCreateModeDialog — choose "Create Default" vs "Use Current"
3. SubstanceAssetDefaultProjectDialog — pick a mesh source for the default project

SubstanceAssetDialog is a simpler variant used when only asset selection
(without the create/open split) is needed.
"""

from __future__ import annotations

from pathlib import Path

import substance_painter as sp
from Qt import QtCore, QtWidgets
from core.util.paths import get_production_path, resolve_mapped_path
from substance_painter.project import NormalMapFormat, ProjectWorkflow, TangentSpace

from core.asset.paths import AssetPaths, paths_for_asset
from core.ui.dialogs import DialogFilteredList, FilteredListDialog
from core.shotgrid import Asset, ShotGrid
from dcc.substance_painter.util.docs import docs_link_html

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIPE_SP_PROJECT_TEMPLATE_NAME = "sandwich_default.spt"
PIPE_SP_PROJECT_TEMPLATE_DIR = Path("painter_assets") / "templates"

# ---------------------------------------------------------------------------
# Helpers used by dialogs and other sp modules
# ---------------------------------------------------------------------------


def project_path_for_variant(paths: AssetPaths, variant: str) -> Path:
    """Return the Substance Painter project file path for a geometry variant."""
    return paths.textures_variant_path(variant)


def resolve_default_mesh_paths(
    paths: AssetPaths,
    *,
    use_custom_mesh: bool,
    custom_mesh_path: Path | None,
    variant: str,
) -> tuple[Path | None, Path | None, Path | None]:
    """Determine which mesh file to use when creating a default project.

    Returns (selected_path, variant_path, fallback_path):
    - *selected_path*: the mesh that should actually be loaded
    - *variant_path*: the expected location for the variant mesh (may not exist)
    - *fallback_path*: an alternative location tried when variant_path is missing
    """
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


def default_project_settings() -> sp.project.Settings:
    """Return the Substance Painter project settings used for new projects."""
    return sp.project.Settings(
        normal_map_format=NormalMapFormat.OpenGL,
        tangent_space_mode=TangentSpace.PerVertex,
        project_workflow=ProjectWorkflow.UVTile,
    )  # type: ignore[call-arg]


def project_template_path() -> Path:
    """Return the expected path to the default Painter project template."""
    return (
        get_production_path()
        / PIPE_SP_PROJECT_TEMPLATE_DIR
        / PIPE_SP_PROJECT_TEMPLATE_NAME
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _geo_variants_for_asset(asset: Asset) -> list[str]:
    """Return a sorted list of geometry variant names, defaulting to ["main"]."""
    variants = sorted(v for v in (asset.geometry_variants or ()) if v)
    if variants:
        return [str(v) for v in variants]
    return ["main"]


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class SubstanceAssetDialog(FilteredListDialog):
    """Simple asset picker that shows the canonical textures path on selection."""

    _conn: ShotGrid
    _info_label: QtWidgets.QLabel

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: ShotGrid
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

        asset = self._conn.get_asset(name=selected)
        if not asset:
            self._info_label.setText("Could not resolve the selected asset.")
            return

        paths = paths_for_asset(asset)
        path = project_path_for_variant(paths, "main")
        status = "exists" if path.exists() else "missing"
        self._info_label.setText(f"Substance Painter project (main): {path} ({status})")


class SubstanceAssetSelectDialog(QtWidgets.QDialog, DialogFilteredList):
    """Pick an asset + geometry variant, then choose to open or create a project."""

    ACTION_OPEN_EXISTING = "open_existing"
    ACTION_CREATE_PROJECT = "create_project"

    _conn: ShotGrid
    _info_label: QtWidgets.QLabel
    _action: str | None
    _asset: Asset | None
    _paths: AssetPaths | None
    _geo_variant_dropdown: QtWidgets.QComboBox

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: ShotGrid
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

        # --- Asset info ---
        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        info_layout.addWidget(self._info_label)
        layout.addWidget(info_widget)

        # --- Geometry variant selector ---
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

        # --- Action buttons ---
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

        # --- Footer ---
        footer = QtWidgets.QLabel(
            "Tip: Select an asset and geometry variant. "
            "Each variant opens its own Substance Painter project file.<br>"
            f"For more information, see {docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        # --- Signals ---
        self._list_widget.itemSelectionChanged.connect(self._on_item_selected)
        self._geo_variant_dropdown.currentTextChanged.connect(self._on_variant_changed)
        self._open_existing_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_OPEN_EXISTING)
        )
        self._create_project_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_CREATE_PROJECT)
        )
        self._update_button_state()

    # -- Public interface --

    def get_selected_action(self) -> str | None:
        return self._action

    def get_selected_asset(self) -> Asset | None:
        return self._asset

    def get_selected_variant(self) -> str:
        return self._geo_variant_dropdown.currentText().strip() or "main"

    # -- Internal --

    def _set_action_and_accept(self, action: str) -> None:
        self._action = action
        self.accept()

    def _on_variant_changed(self, _text: str) -> None:
        self._update_project_info()
        self._update_button_state()

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._asset = None
            self._paths = None
            self._geo_variant_dropdown.clear()
            self._info_label.setText("Select an asset to see details.")
            self._update_button_state()
            return

        asset = self._conn.get_asset(name=selected)
        if not asset:
            self._asset = None
            self._paths = None
            self._geo_variant_dropdown.clear()
            self._info_label.setText("Could not resolve the selected asset.")
            self._update_button_state()
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
        self._update_button_state()

    def _selected_project_path(self) -> Path | None:
        if not self._paths:
            return None
        return project_path_for_variant(self._paths, self.get_selected_variant())

    def _update_project_info(self) -> None:
        path = self._selected_project_path()
        if not path:
            return
        variant = self.get_selected_variant()
        status = "exists" if path.exists() else "missing"
        self._info_label.setText(
            f"Geometry variant: {variant}\nSubstance Painter project: {path} ({status})"
        )

    def _update_button_state(self) -> None:
        has_asset = bool(self._asset)
        self._geo_variant_dropdown.setEnabled(has_asset)
        path = self._selected_project_path()
        project_exists = bool(path and path.exists())
        self._open_existing_btn.setEnabled(project_exists)
        self._create_project_btn.setEnabled(has_asset)


class SubstanceAssetCreateModeDialog(QtWidgets.QDialog):
    """Choose how to create the Substance Painter project for an asset.

    Options:
    - **Create Default Project**: use a template + published mesh
    - **Use Currently Open Project**: save the current project to this asset
    """

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

        # --- Action buttons ---
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

        # --- Footer ---
        footer = QtWidgets.QLabel(
            'Tip: use "Create Default Project" unless you have talked with your team lead. '
            "The project will be saved to the selected geometry variant file.<br>"
            f"For more information, see {docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        # --- Signals ---
        self._create_default_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_CREATE_DEFAULT)
        )
        self._use_current_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_USE_CURRENT)
        )
        self._use_current_btn.setEnabled(sp.project.is_open())

    def get_selected_action(self) -> str | None:
        return self._action

    def _set_action_and_accept(self, action: str) -> None:
        self._action = action
        self.accept()


class SubstanceAssetDefaultProjectDialog(QtWidgets.QDialog):
    """Pick a geometry source (published variant or custom file) for a new project."""

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

        # --- Published variant option ---
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

        # --- Custom mesh option ---
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

        # --- Mesh status ---
        self._mesh_status_label = QtWidgets.QLabel("Mesh source: --")
        self._mesh_status_label.setWordWrap(True)
        self._mesh_status_label.setTextFormat(QtCore.Qt.PlainText)
        self._mesh_status_label.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._mesh_status_label)

        # --- Create button ---
        buttons_layout = QtWidgets.QHBoxLayout()
        self._create_default_btn = QtWidgets.QPushButton("Create Default Project")
        self._create_default_btn.setToolTip(
            "Create a new project using the selected mesh source."
        )
        buttons_layout.addWidget(self._create_default_btn)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)

        # --- Footer ---
        footer = QtWidgets.QLabel(
            "Tip: The selected geometry variant is locked for this project file. "
            "Use Custom Mesh to browse for any file.<br>"
            f"For more information, see {docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(footer)

        # --- Signals ---
        self._custom_mesh_field.textChanged.connect(self._update_mesh_status)
        self._geo_variant_radio.toggled.connect(self._on_mesh_source_toggled)
        self._custom_mesh_radio.toggled.connect(self._on_mesh_source_toggled)
        self._custom_mesh_browse.clicked.connect(self._browse_custom_mesh)
        self._create_default_btn.clicked.connect(self.accept)
        self._on_mesh_source_toggled()

    # -- Public interface --

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

    # -- Internal --

    def _on_mesh_source_toggled(self) -> None:
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
        custom_mesh = self.get_custom_mesh_path()

        resolved, variant_path, fallback_path = resolve_default_mesh_paths(
            self._paths,
            use_custom_mesh=use_custom,
            custom_mesh_path=custom_mesh,
            variant=self._geo_variant,
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
                    f"Mesh source: {variant_path} (missing; fallback {fallback_path} missing)"
                )
            else:
                self._mesh_status_label.setText(f"Mesh source: {resolved} (missing)")

        mesh_ready = resolved is not None and resolved.exists()
        self._create_default_btn.setEnabled(mesh_ready)
