from __future__ import annotations

import logging
import os
from math import log2
from pathlib import Path
from re import findall
from typing import TYPE_CHECKING

from Qt import QtCore, QtWidgets
from Qt.QtCore import QRegExp
from Qt.QtGui import QIcon, QPixmap, QRegExpValidator
from Qt.QtWidgets import (
    QComboBox,
    QLabel,
    QLayout,
    QMainWindow,
    QScrollArea,
)

if TYPE_CHECKING:
    import typing

import substance_painter as sp
from env_sg import DB_Config
from shared.util import get_documentation_path

from pipe.asset.paths import DCC_SUBSTANCE, paths_for_asset
from pipe.asset.versioning import backup_if_changed
from pipe.db import DB
from pipe.glui.dialogs import ButtonPair, MessageDialog
from pipe.sp.assetfile import PIPE_SP_DOCS_PAGE, get_active_asset_from_project
from pipe.sp.export import Exporter, TexSetExportSettings
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset
from pipe.struct.material import DisplacementSource, NormalSource, NormalType
from pipe.util import checkbox_callback_helper, dict_index

log = logging.getLogger(__name__)


def _docs_link_html() -> str:
    url = get_documentation_path(PIPE_SP_DOCS_PAGE)
    if "://" not in url:
        url = QtCore.QUrl.fromLocalFile(url).toString()
    return f'<a href="{url}">the documentation</a>'


class SubstanceExportWindow(QMainWindow, ButtonPair):
    _curr_asset: Asset
    _central_widget: QtWidgets.QWidget
    _conn: DB
    _main_layout: QLayout
    _mat_var_dropdown: QComboBox
    _geo_var_dropdown: QComboBox
    _shader_layer_dropdown: QComboBox

    # _mat_var_enabled: QtWidgets.QCheckBox
    # _metadataManager: pipe.sp.metadata.MetadataUpdater
    _tex_set_dict: dict[sp.textureset.TextureSet, "TexSetWidget"]

    def __init__(self, flags: QtCore.Qt.WindowFlags | None = None) -> None:
        super(SubstanceExportWindow, self).__init__(get_main_qt_window())

        self._tex_set_dict = {}

        self._conn = DB.Get(DB_Config)
        if not sp.project.is_open():
            MessageDialog(
                get_main_qt_window(),
                "No Substance Painter project is open. Open a project first.",
            ).exec_()
            self.close()
            return

        self._curr_asset = get_active_asset_from_project(self._conn)
        if not self._curr_asset:
            MessageDialog(
                get_main_qt_window(),
                "Could not resolve the current asset from project metadata. "
                "Use Open Asset to create or open the asset project first.",
            ).exec_()
            self.close()
            return

        if not self._curr_asset.tex_path:
            MessageDialog(
                get_main_qt_window(),
                "This asset does not have a textures path set in ShotGrid.",
            ).exec_()
            self.close()
            return

        self._setup_publish_ui()

    def _setup_publish_ui(self) -> None:
        self.setWindowTitle("Publish Textures")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(560, 700)

        self._central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self._central_widget)
        self._main_layout = QtWidgets.QVBoxLayout(self._central_widget)
        self._main_layout.setContentsMargins(12, 12, 12, 12)
        self._main_layout.setSpacing(8)

        title = QLabel("Publish Textures")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self._main_layout.addWidget(title)

        asset_display_name = (
            self._curr_asset.display_name or self._curr_asset.name or "Unknown Asset"
        )
        asset_label = QLabel(f"Asset: {asset_display_name}")
        asset_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        asset_label.setToolTip(
            "Resolved from project metadata saved by the Open Asset tool."
        )
        self._main_layout.addWidget(asset_label)

        lock_warning = QLabel(
            "<b>Heads up:</b> If this asset is open in Houdini on Windows, "
            "stop the render and press <b>Reset RenderMan RIS/XPU</b> before "
            "exporting or TEX conversion can fail."
        )
        lock_warning.setWordWrap(True)
        lock_warning.setStyleSheet("color: #d28d42;")
        self._main_layout.addWidget(lock_warning)

        texture_set_layout = QtWidgets.QVBoxLayout()
        for tex_set in sp.textureset.all_texture_sets():
            widget = TexSetWidget(self, tex_set)
            self._tex_set_dict[tex_set] = widget
            texture_set_layout.addWidget(widget)

        texture_set_widget = QtWidgets.QWidget()
        texture_set_widget.setLayout(texture_set_layout)
        texture_set_scroll_area = QtWidgets.QScrollArea()
        texture_set_scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff
        )
        texture_set_scroll_area.setWidget(texture_set_widget)
        texture_set_scroll_area.setWidgetResizable(True)
        self._main_layout.addWidget(texture_set_scroll_area, 1)

        self._mat_var_dropdown = self._build_variant_dropdown(
            label_text="Material Variant:",
            tooltip=(
                "Material variant name used in the publish folder. "
                "Type a new name to create a variant."
            ),
            items=self._variant_items(self._curr_asset.material_variants, "default"),
            default_value="default",
            editable=True,
            validator=QRegExpValidator(QRegExp("[a-z][a-z_\\d]*")),
        )

        geo_items = sorted(self._curr_asset.geometry_variants) or ["main"]
        geo_default = "main" if "main" in geo_items else geo_items[0]
        self._geo_var_dropdown = self._build_variant_dropdown(
            label_text="Geometry Variant:",
            tooltip=("Geometry variant to match the published model."),
            items=geo_items,
            default_value=geo_default,
            editable=False,
        )

        self._shader_layer_dropdown = self._build_variant_dropdown(
            label_text="Shader Layer:",
            tooltip=("Shader layer name used for layered materials."),
            items=self._variant_items(self._curr_asset.render_variants, "default"),
            default_value="default",
            editable=True,
            validator=QRegExpValidator(QRegExp("[a-z][a-z_\\d]*")),
        )

        self._init_buttons(has_cancel_button=True, ok_name="Export")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setToolTip(
            "Export textures and convert them to TEX/preview files."
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.Cancel).setToolTip(
            "Close without exporting."
        )
        self._main_layout.addWidget(self.buttons)

        footer = QLabel(
            "Tip: Make sure your project was opened via Open Asset so the asset "
            "metadata is stored in the project. For more information, see "
            f"{_docs_link_html()}."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        footer.setOpenExternalLinks(True)
        footer.setStyleSheet("color: #8a8a8a;")
        self._main_layout.addWidget(footer)

    def _build_variant_dropdown(
        self,
        *,
        label_text: str,
        tooltip: str,
        items: list[str],
        default_value: str,
        editable: bool,
        validator: QRegExpValidator | None = None,
    ) -> QComboBox:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setToolTip(tooltip)
        layout.addWidget(label, 30)

        dropdown = QComboBox()
        dropdown.addItems(items)
        dropdown.setCurrentText(default_value)
        dropdown.setEditable(editable)
        dropdown.setToolTip(tooltip)
        if validator is not None:
            dropdown.setValidator(validator)
        layout.addWidget(dropdown, 70)
        self._main_layout.addWidget(widget)
        return dropdown

    @staticmethod
    def _variant_items(options: list[str], default_value: str) -> list[str]:
        items = set(options)
        items.add(default_value)
        return sorted(items)

    def _preflight(self) -> bool:
        """Check for asset metadata and correct channel types before running
        the export"""
        return True
        # metaUpdater = pipe.sp.metadata.MetadataUpdater()
        # meta = metaUpdater.check() or metaUpdater.do_update()
        # return meta  # and srgb

    @property
    def mat_var(self) -> str:
        return self._mat_var_dropdown.currentText()

    @property
    def geo_var(self) -> str:
        return self._geo_var_dropdown.currentText()

    @property
    def shader_layer(self) -> str:
        return self._shader_layer_dropdown.currentText()

    def do_export(self, isBatch: bool = False) -> None:
        if not self._curr_asset:
            return

        if self.mat_var not in self._curr_asset.material_variants:
            self._curr_asset.material_variants.add(self.mat_var)
            log.info(f"Updating new material variant: {self.mat_var}")
            self._conn.update_asset(self._curr_asset)

        if self.shader_layer not in self._curr_asset.render_variants:
            self._curr_asset.render_variants.add(self.shader_layer)
            log.info(f"Updating new shader layer: {self.shader_layer}")
            self._conn.update_asset(self._curr_asset)

        log.info("Exporting!")
        exporter = Exporter(self._curr_asset)
        export_settings = [
            TexSetExportSettings(
                ts,
                wgt.extra_channels,
                wgt.resolution,
                wgt.displacement_source,
                wgt.normal_type,
                wgt.normal_source,
            )
            for ts, wgt in self._tex_set_dict.items()
            if wgt.enabled
        ]
        if not export_settings:
            MessageDialog(
                get_main_qt_window(),
                "No texture sets are enabled for export.",
            ).exec_()
            return

        if exporter.export(
            export_settings,
            self.mat_var,
            self.geo_var,
            self.shader_layer,
        ):
            backup_status = None
            project_path = sp.project.file_path() or ""
            if not project_path:
                backup_status = "Backup skipped: project has no file path."
                log.warning("Backup skipped: project has no file path.")
            else:
                asset_paths = paths_for_asset(self._curr_asset)
                publish_path = asset_paths.publish_textures_layer_dir(
                    self.geo_var, self.mat_var, self.shader_layer
                )
                result = backup_if_changed(
                    source_path=Path(project_path),
                    backup_dir=asset_paths.backup_dir,
                    manifest_path=asset_paths.manifest_path,
                    dcc=DCC_SUBSTANCE,
                    publish_path=publish_path,
                    extra={
                        "geo": self.geo_var,
                        "material": self.mat_var,
                        "shader_layer": self.shader_layer,
                    },
                    asset_name=self._curr_asset.display_name or self._curr_asset.name,
                    asset_path=self._curr_asset.path,
                    asset_id=self._curr_asset.id,
                )

                if result is None:
                    backup_status = "Backup skipped: source file missing."
                    log.warning("Backup skipped: source file missing.")
                elif result.changed:
                    if result.backup_path:
                        backup_status = f"Backup created: {result.backup_path.name}"
                        log.info("Backup created at %s", result.backup_path)
                    else:
                        backup_status = "Backup created."
                        log.info("Backup created for %s", project_path)
                else:
                    backup_status = "Backup skipped: no changes detected."
                    log.info("Backup skipped: no changes detected.")

            message = "Textures successfully exported!"
            if backup_status:
                message = f"{message}\n{backup_status}"
            MessageDialog(
                get_main_qt_window(),
                message,
            ).exec_()
        else:
            MessageDialog(
                get_main_qt_window(),
                (
                    "An error occured while exporting textures. Please check the "
                    "console for more information"
                ),
            ).exec_()


class TexSetWidget(QtWidgets.QWidget):
    extra_channels: set[sp.textureset.Channel]

    _displacement_source_dropdown: QComboBox
    _enabled_checkbox: QtWidgets.QCheckBox
    _extra_channels_layout: QLayout
    _help_icon: QIcon
    _parent_window: SubstanceExportWindow
    _normal_source_dropdown: QComboBox
    _normal_type_dropdown: QComboBox
    _resolution_dropdown: QComboBox
    _settings_container: QtWidgets.QWidget
    _stack: sp.textureset.Stack
    _tex_set: sp.textureset.TextureSet

    DEFAULT_CHANNELS = [
        sp.textureset.ChannelType.BaseColor,
        sp.textureset.ChannelType.Height,
        sp.textureset.ChannelType.Roughness,
        sp.textureset.ChannelType.Opacity,
        sp.textureset.ChannelType.Emissive,
        sp.textureset.ChannelType.Metallic,
        sp.textureset.ChannelType.Normal,
        sp.textureset.ChannelType.Displacement,
    ]

    _NORM_TYPE_STRS = {
        NormalType.STANDARD: "Standard (default)",
        NormalType.BUMP_ROUGHNESS: "Bump Roughness",
    }

    _NORM_SOURCE_STRS = {
        NormalSource.NORMAL_HEIGHT: "Normal + Height (default)",
        NormalSource.NORMAL_ONLY: "Normal Only",
    }

    _DISP_SOURCE_STRS = {
        DisplacementSource.NONE: "None (default)",
        DisplacementSource.HEIGHT: "Height",
        DisplacementSource.DISPLACEMENT: "Displacement",
    }

    def __init__(
        self,
        parent: SubstanceExportWindow,
        tex_set: sp.textureset.TextureSet,
        flags: QtCore.Qt.WindowFlags | None = None,
    ) -> None:
        super().__init__(parent)
        self.setParent(parent)
        self._parent_window = parent
        self._tex_set = tex_set
        self.extra_channels = set()
        self._help_icon = QIcon(
            QPixmap(os.getenv("PIPE_PATH", "") + "/lib/icon/material-help.svg")
        )

        try:
            self._stack = self._tex_set.get_stack()
        except ValueError:
            MessageDialog(
                get_main_qt_window(),
                (
                    "Warning! Could not get material stacks! You are doing "
                    "something cool with material layering. Please show this to "
                    "Dallin so he can fix it."
                ),
            ).exec_()

        self._setup_ui()

    def _info_tooltip(self, message: str) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setIcon(self._help_icon)
        button.setStyleSheet("background-color: #00000000; border: none;")
        button.setToolTip(message)
        return button

    @staticmethod
    def _get_default(items: typing.Iterable[str]) -> str:
        return next((i for i in items if i.endswith("(default)")), "")

    def _setup_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(QtCore.Qt.AlignTop)

        # Enable/disable checkbox and set up layouts
        self._enabled_checkbox = QtWidgets.QCheckBox()
        self._enabled_checkbox.setChecked(True)
        self._enabled_checkbox.setStyleSheet("padding-top: 10px;")
        self._enabled_checkbox.setToolTip("Include this texture set in the export.")
        layout.addWidget(self._enabled_checkbox, 10, QtCore.Qt.AlignTop)
        settings_container = QtWidgets.QWidget()
        self._enabled_checkbox.toggled.connect(
            checkbox_callback_helper(self._enabled_checkbox, settings_container)
        )
        settings_layout = QtWidgets.QGridLayout(settings_container)
        settings_layout.setSpacing(2)
        layout.addWidget(settings_container, 90)

        # Texture set title
        self.label = QLabel(self._tex_set.name())
        self.label.setStyleSheet("font-size: 11px; font-weight: bold;")
        settings_layout.addWidget(self.label, 0, 0, 1, 3)

        # Extra channels
        extra_channels = QtWidgets.QWidget()
        self._extra_channels_layout = QtWidgets.QHBoxLayout(extra_channels)
        if self._setup_extra_channel_layout():
            settings_layout.addWidget(QLabel("Extra Maps:"), 1, 0)
            settings_layout.addWidget(extra_channels)

        # Resolution selection
        settings_layout.addWidget(QLabel("Resolution:"), 2, 0)
        self._resolution_dropdown = QComboBox()
        self._resolution_dropdown.addItems(
            ["128", "256", "512", "1024", "2048", "4096"]
        )
        current_res_log2 = int(log2(self._tex_set.get_resolution().width))
        self._resolution_dropdown.setCurrentIndex(current_res_log2 - 7)
        settings_layout.addWidget(self._resolution_dropdown)

        # Normal map source
        settings_layout.addWidget(QLabel("Normal Map Source:"), 3, 0)
        self._normal_source_dropdown = QComboBox()
        ns_items = self._NORM_SOURCE_STRS.values()
        self._normal_source_dropdown.addItems(ns_items)
        self._normal_source_dropdown.setCurrentText(self._get_default(ns_items))
        settings_layout.addWidget(self._normal_source_dropdown)
        settings_layout.addWidget(
            self._info_tooltip(
                "Substance's default behavior is to convert the Height channel "
                "to a normal map, then combine it with the Normal channel. \n"
                '"Normal + Height" keeps this behavior. \n'
                '"Normal Only" does not combine in the Height channel.'
            )
        )

        # Normal map type
        settings_layout.addWidget(QLabel("Normal Map Type:"), 4, 0)
        self._normal_type_dropdown = QComboBox()
        nt_items = self._NORM_TYPE_STRS.values()
        self._normal_type_dropdown.addItems(nt_items)
        self._normal_type_dropdown.setCurrentText(self._get_default(nt_items))
        settings_layout.addWidget(self._normal_type_dropdown)
        settings_layout.addWidget(
            self._info_tooltip(
                "Bump Roughness mapping preserves detail in shiny items with "
                "variance/breakup in the roughness (i.e. scratches, smudges, "
                "etc.). \n"
                "Select Bump Roughness if your texture set is a shiny "
                "material with variance/breakup in the roughness. Otherwise, "
                "leave it on Standard."
            )
        )

        # Displacement map source
        settings_layout.addWidget(QLabel("Displacement Map Source:"), 5, 0)
        self._displacement_source_dropdown = QComboBox()
        ds_items = list(self._DISP_SOURCE_STRS.values())
        self._displacement_source_dropdown.addItems(ds_items)
        self._displacement_source_dropdown.setCurrentText(self._get_default(ds_items))
        if sp.textureset.ChannelType.Displacement in self._stack.all_channels().keys():
            self._displacement_source_dropdown.setCurrentText(
                self._DISP_SOURCE_STRS[DisplacementSource.DISPLACEMENT]
            )
        else:
            self._displacement_source_dropdown.removeItem(
                ds_items.index(self._DISP_SOURCE_STRS[DisplacementSource.DISPLACEMENT])
            )
        settings_layout.addWidget(self._displacement_source_dropdown)
        settings_layout.addWidget(
            self._info_tooltip(
                "Displacement is expensive and should only be used on assets "
                "that will be close enough to the camera that the changes to "
                "the silhouette will be noticeable. You can source the "
                "displacement map from the Height channel, or from the "
                "Displacement channel."
            )
        )

        self.setLayout(layout)

    def _setup_extra_channel_layout(self) -> bool:
        """Sets up extra channel layout. Returns False if there are no extra channels"""
        has_channels: bool = False
        for channel_type, channel in self._stack.all_channels().items():
            if channel_type not in self.DEFAULT_CHANNELS:
                # get channel name
                name = (
                    getattr(channel, "label", None)
                    and channel.label().title().replace(" ", "")
                    or channel.type().name
                )
                # add spaces
                name = " ".join(
                    findall(r"[A-Z0-9](?:[a-z0-9]+|[A-Z]*(?=[A-Z]|$))", name)
                )
                # set up checkboxes
                checkbox = QtWidgets.QCheckBox(name)
                checkbox.setChecked(False)
                checkbox.stateChanged.connect(self._extra_channels_updater(channel))
                self._extra_channels_layout.addWidget(checkbox)
                has_channels = True

        return has_channels

    def _extra_channels_updater(
        self, ch: sp.textureset.Channel
    ) -> typing.Callable[[], None]:
        """Callback function generator for extra channels checkboxes"""

        def inner() -> None:
            if ch in self.extra_channels:
                self.extra_channels.remove(ch)
            else:
                self.extra_channels.add(ch)

        return inner

    @property
    def enabled(self) -> bool:
        return self._enabled_checkbox.isChecked()

    @property
    def resolution(self) -> int:
        """Returns the resolution log 2"""
        return self._resolution_dropdown.currentIndex() + 7

    @property
    def normal_type(self) -> NormalType:
        return dict_index(
            self._NORM_TYPE_STRS, self._normal_type_dropdown.currentText()
        )

    @property
    def normal_source(self) -> NormalSource:
        return dict_index(
            self._NORM_SOURCE_STRS, self._normal_source_dropdown.currentText()
        )

    @property
    def displacement_source(self) -> DisplacementSource:
        return dict_index(
            self._DISP_SOURCE_STRS,
            self._displacement_source_dropdown.currentText(),
        )
