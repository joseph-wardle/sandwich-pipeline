from __future__ import annotations

import logging
import os
from math import log2
from re import findall
from typing import TYPE_CHECKING

from PySide6.QtCore import QEventLoop  # type: ignore[import-not-found]
from Qt import QtCore, QtWidgets
from Qt.QtCore import QRegExp
from Qt.QtGui import QIcon, QPixmap, QRegExpValidator
from Qt.QtWidgets import (
    QComboBox,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    import typing

import substance_painter as sp
from env_sg import DB_Config

from pipe.db import DB
from pipe.glui.dialogs import ButtonPair, MessageDialog
from pipe.sp.assetfile import (
    get_asset_selection_metadata,
    store_asset_selection_metadata,
)
from pipe.sp.export import Exporter, TexSetExportSettings
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset
from pipe.struct.material import DisplacementSource, NormalSource, NormalType
from pipe.util import checkbox_callback_helper, dict_index

log = logging.getLogger(__name__)


class SubstanceExportWindow(QMainWindow, ButtonPair):
    _curr_asset: Asset
    _central_widget: QtWidgets.QWidget
    _conn: DB
    _main_layout: QLayout
    _mat_var_dropdown: QComboBox
    _geo_var_dropdown: QComboBox
    _render_var_dropdown: QComboBox

    # _mat_var_enabled: QtWidgets.QCheckBox
    # _metadataManager: pipe.sp.metadata.MetadataUpdater
    _tex_set_dict: dict[sp.textureset.TextureSet, "TexSetWidget"]
    _tex_set_widgets: list["TexSetWidget"]
    _tex_set_dropdowns: dict[sp.textureset.TextureSet, QComboBox]
    _tex_set_asset_dict: dict[
        str, list[sp.textureset.TextureSet]
    ]  # links assets to lists of texture sets

    def __init__(self, flags: QtCore.Qt.WindowFlags | None = None) -> None:
        super(SubstanceExportWindow, self).__init__(get_main_qt_window())

        self._tex_set_dict = {}
        self._tex_set_widgets = []
        self._tex_set_dropdowns = {}
        self._tex_set_asset_dict = {}

        self._conn = DB.Get(DB_Config)

        self.setup_choose_assets_ui()

    def setup_choose_assets_ui(self):
        self.setWindowTitle("Choose Assets")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        # Set up main layout
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)
        self._main_layout = QVBoxLayout(self._central_widget)

        # Title label
        title = QLabel("Choose the assets you are using")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self._main_layout.addWidget(title)

        # Container for texture set layout
        texture_set_layout = QVBoxLayout()

        # Get asset names (assumed sorted)
        asset_display_names = self._conn.get_asset_display_name_list(sorted=True)

        selection_metadata = get_asset_selection_metadata()
        metadata_map = selection_metadata.get("asset_map") or {}
        default_asset = selection_metadata.get("last_asset")

        if default_asset and default_asset in asset_display_names:
            metadata_label = QLabel(
                f"Defaulting selection to {default_asset} from project metadata."
            )
            metadata_label.setWordWrap(True)
            metadata_label.setStyleSheet("color: #9fb4ff;")
            self._main_layout.addWidget(metadata_label)

        # Add widgets for each texture set
        for ts in sp.textureset.all_texture_sets():
            ts_label = QLabel(ts.name())
            ts_label.setStyleSheet("font-size: 12px; font-weight: normal;")
            texture_set_layout.addWidget(ts_label)

            # Search box for filtering assets
            search_box = QLineEdit()
            search_box.setPlaceholderText("Search assets...")
            search_box.textChanged.connect(
                lambda text, ts=ts: self.filter_assets(text, ts)
            )
            texture_set_layout.addWidget(search_box)

            # Dropdown to select asset
            asset_dropdown = QComboBox()
            asset_dropdown.addItems(asset_display_names)
            preferred = metadata_map.get(ts.name(), default_asset)
            if preferred and preferred in asset_display_names:
                asset_dropdown.setCurrentText(preferred)

            # Store dropdown in a dictionary
            self._tex_set_dropdowns[ts] = asset_dropdown

            texture_set_layout.addWidget(asset_dropdown)

        # Create a container for the texture set layout
        texture_set_widget = QWidget()
        texture_set_widget.setLayout(texture_set_layout)

        # Scroll area to handle large content
        texture_set_scroll_area = QScrollArea()
        texture_set_scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff
        )
        texture_set_scroll_area.setWidget(texture_set_widget)
        texture_set_scroll_area.setWidgetResizable(True)

        # Add the scroll area to the main layout
        self._main_layout.addWidget(texture_set_scroll_area)

        # Add the confirm button
        confirm_button = QPushButton("Confirm", self)
        confirm_button.clicked.connect(self.on_confirm_button_clicked)
        self._main_layout.addWidget(confirm_button)

    def filter_assets(self, text: str, texture_set) -> None:
        """
        Filters the assets in the dropdown based on the search text.
        """
        asset_dropdown = self._tex_set_dropdowns.get(texture_set)
        if asset_dropdown:
            # Filter the items based on the text
            filtered_assets = [
                asset
                for asset in self._conn.get_asset_display_name_list()
                if text.lower() in asset.lower()
            ]
            asset_dropdown.clear()  # Clear existing items
            asset_dropdown.addItems(filtered_assets)  # Add filtered items

    def on_confirm_button_clicked(self):
        self._tex_set_asset_dict = {}

        for ts, dropdown in self._tex_set_dropdowns.items():
            selected_asset_display_name = dropdown.currentText()

            if selected_asset_display_name not in self._tex_set_asset_dict.keys():
                self._tex_set_asset_dict[selected_asset_display_name] = []

            self._tex_set_asset_dict[selected_asset_display_name].append(ts)

        asset_map = {
            ts.name(): asset_name
            for asset_name, tex_sets in self._tex_set_asset_dict.items()
            for ts in tex_sets
        }
        store_asset_selection_metadata(asset_map)

        self.clear_layout()
        self._setup_asset_ui()

    def clear_layout(self):
        layout = self._main_layout

        # Function to delete all child widgets in the layout recursively
        def delete_items(layout):
            if layout:
                index = 0
                while True:
                    item = layout.itemAt(index)
                    if not item:
                        break  # No more items to remove
                    widget = item.widget()
                    if widget:
                        widget.deleteLater()  # Delete the widget
                    else:
                        # If the item is a nested layout, recursively delete its children
                        delete_items(item.layout())
                    index += 1

        # Clear the layout
        delete_items(layout)

        self.resize(400, 600)

    def _setup_asset_ui(self):
        # loops though every asset that was selected in the first prompt, and lets them set export options per texture set
        for i, curr_asset_display_name in enumerate(self._tex_set_asset_dict.keys()):
            self.clear_layout()

            self.setWindowTitle(f"{curr_asset_display_name} Publish Textures")

            # Make sure window always stays on top
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

            # Set up main layout
            self._central_widget = QtWidgets.QWidget()
            self.setCentralWidget(self._central_widget)
            self._main_layout = QtWidgets.QVBoxLayout()
            self._central_widget.setLayout(self._main_layout)

            # title

            # File lock warning
            lock_warning = QLabel(
                '<a style="color: orangered"><b>WARNING:</b></a> If you '
                "currently have this asset open in Houdini on Windows, you "
                '<b>MUST</b> stop your render and press "Reset Renderman RIS / '
                'XPU" before exporting or TEX file conversion will not work!'
            )
            lock_warning.setWordWrap(True)
            self._main_layout.addWidget(lock_warning)

            self._curr_asset = self._conn.get_asset_by_display_name(
                curr_asset_display_name
            )

            # Texture set widgets
            texture_set_layout = QtWidgets.QVBoxLayout()
            for ts in self._tex_set_asset_dict[curr_asset_display_name]:
                widget = TexSetWidget(self, ts)
                self._tex_set_dict[ts] = widget
                texture_set_layout.addWidget(widget)

            texture_set_widget = QtWidgets.QWidget()
            texture_set_widget.setLayout(texture_set_layout)
            texture_set_scroll_area = QtWidgets.QScrollArea()
            texture_set_scroll_area.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarAlwaysOff
            )
            texture_set_scroll_area.setWidget(texture_set_widget)
            texture_set_scroll_area.setWidgetResizable(True)
            self._main_layout.addWidget(texture_set_scroll_area)

            # Material Variants
            mat_var_widget = QtWidgets.QWidget()
            mat_var_layout = QtWidgets.QHBoxLayout(mat_var_widget)
            mat_var_layout.setContentsMargins(0, 0, 0, 0)
            mat_var_layout.setSpacing(0)
            mat_var_settings_widget = QtWidgets.QWidget()
            mat_var_settings_layout = QtWidgets.QHBoxLayout(mat_var_settings_widget)
            mat_var_label = QLabel("Material Variant:")
            mat_var_settings_layout.addWidget(mat_var_label, 30)
            self._mat_var_dropdown = QComboBox()
            mv_set = set(self._curr_asset.material_variants)
            mv_set.add("default")
            mv_items = list(mv_set)
            self._mat_var_dropdown.addItems(mv_items)
            self._mat_var_dropdown.setCurrentText("default")
            self._mat_var_dropdown.setEditable(True)
            pattern = QRegExp("[a-z][a-z_\d]*")
            mat_var_validator = QRegExpValidator(pattern)
            self._mat_var_dropdown.setValidator(mat_var_validator)
            mat_var_settings_layout.addWidget(self._mat_var_dropdown, 70)
            mat_var_layout.addWidget(mat_var_settings_widget, 90)
            self._main_layout.addWidget(mat_var_widget)

            # Geometry Variants
            geo_var_widget = QtWidgets.QWidget()
            geo_var_layout = QtWidgets.QHBoxLayout(geo_var_widget)
            geo_var_layout.setContentsMargins(0, 0, 0, 0)
            geo_var_layout.setSpacing(0)
            geo_var_settings_widget = QtWidgets.QWidget()
            geo_var_settings_layout = QtWidgets.QHBoxLayout(geo_var_settings_widget)
            geo_var_label = QLabel("Geometry Variant:")
            geo_var_settings_layout.addWidget(geo_var_label, 30)
            self._geo_var_dropdown = QComboBox()
            mv_set = set(self._curr_asset.geometry_variants)
            mv_items = list(mv_set)
            self._geo_var_dropdown.addItems(mv_items)
            self._geo_var_dropdown.setCurrentText("main")
            geo_var_settings_layout.addWidget(self._geo_var_dropdown, 70)
            geo_var_layout.addWidget(geo_var_settings_widget, 90)
            self._main_layout.addWidget(geo_var_widget)

            # Renderman Variants
            render_var_widget = QtWidgets.QWidget()
            render_var_layout = QtWidgets.QHBoxLayout(render_var_widget)
            render_var_layout.setContentsMargins(0, 0, 0, 0)
            render_var_layout.setSpacing(0)
            render_var_settings_widget = QtWidgets.QWidget()
            render_var_settings_layout = QtWidgets.QHBoxLayout(
                render_var_settings_widget
            )
            render_var_label = QLabel("Renderman Variant:")
            render_var_settings_layout.addWidget(render_var_label, 30)
            self._render_var_dropdown = QComboBox()
            mv_set = set(self._curr_asset.render_variants)
            mv_set.add("default")

            mv_items = list(mv_set)
            self._render_var_dropdown.addItems(mv_items)
            self._render_var_dropdown.setCurrentText("default")
            self._render_var_dropdown.setEditable(True)
            pattern = QRegExp("[a-z][a-z_\d]*")
            render_var_validator = QRegExpValidator(pattern)
            self._render_var_dropdown.setValidator(
                render_var_validator
            )  # TODO rename this
            render_var_settings_layout.addWidget(self._render_var_dropdown, 70)
            render_var_layout.addWidget(render_var_settings_widget, 90)
            self._main_layout.addWidget(render_var_widget)

            # Buttons
            self._init_buttons(has_cancel_button=True, ok_name="Export")

            self.buttons.rejected.connect(self.close)
            self.buttons.accepted.connect(self.do_export)

            # Wait for a button to be pressed
            event_loop = QEventLoop(self)
            self.buttons.accepted.connect(event_loop.quit)
            self.buttons.rejected.connect(event_loop.quit)

            self._main_layout.addWidget(self.buttons)

            event_loop.exec_()

        self.close()

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
    def render_var(self) -> str:
        return self._render_var_dropdown.currentText()

    def do_export(self, isBatch: bool = False) -> None:
        if self.mat_var not in self._curr_asset.material_variants:
            self._curr_asset.material_variants.add(self.mat_var)
            log.info(f"Updating new material variant: {self.mat_var}")
            self._conn.update_asset(self._curr_asset)

        if self.render_var not in self._curr_asset.render_variants:
            self._curr_asset.render_variants.add(self.render_var)
            log.info(f"Updating new render variant: {self.render_var}")
            self._conn.update_asset(self._curr_asset)

        log.info("Exporting!")
        exporter = Exporter(self._curr_asset)

        if exporter.export(
            [
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
            ],
            self.mat_var,
            self.geo_var,
            self.render_var,
        ):
            MessageDialog(
                get_main_qt_window(),
                "Textures successfully exported!",
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
