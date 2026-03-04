from __future__ import annotations

import json
import logging
import os
import subprocess
from math import log2
from pathlib import Path
from re import findall
from typing import TYPE_CHECKING, Any

from Qt import QtCore, QtWidgets
from Qt.QtCore import QRegExp
from Qt.QtGui import QIcon, QPixmap, QRegExpValidator
from Qt.QtWidgets import (
    QComboBox,
    QLabel,
    QLayout,
    QMainWindow,
)

if TYPE_CHECKING:
    import typing

import substance_painter as sp
from env import Executables
from env_sg import DB_Config
from shared.util import get_documentation_path
from software.houdini.dcc import HoudiniDCC

from pipe.asset.paths import DCC_SUBSTANCE, paths_for_asset
from pipe.asset.versioning import backup_if_changed
from pipe.db import DB
from pipe.glui.dialogs import ButtonPair, MessageDialog, MessageDialogCustomButtons
from pipe.sp.assetfile import PIPE_SP_DOCS_PAGE, get_active_asset_from_project
from pipe.sp.export import Exporter, TexSetExportSettings
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset
from pipe.struct.material import DisplacementSource, NormalSource, NormalType
from pipe.util import checkbox_callback_helper, dict_index

log = logging.getLogger(__name__)
_HOUDINI_RESULT_START = "--BUILD-RESULT--"
_HOUDINI_RESULT_END = "--END-BUILD-RESULT--"


class HoudiniPublishError(RuntimeError):
    """Raised when headless Houdini publish fails from Substance."""


def _docs_link_html() -> str:
    url = get_documentation_path(PIPE_SP_DOCS_PAGE)
    if "://" not in url:
        url = QtCore.QUrl.fromLocalFile(url).toString()
    return f'<a href="{url}">the documentation</a>'


def _texture_set_name(tex_set: sp.textureset.TextureSet) -> str:
    name_attr = getattr(tex_set, "name", None)
    if callable(name_attr):
        return name_attr()
    if isinstance(name_attr, str):
        return name_attr
    return str(tex_set)


class SubstanceExportWindow(QMainWindow, ButtonPair):
    _curr_asset: Asset | None
    _central_widget: QtWidgets.QWidget
    _conn: DB
    _main_layout: QLayout
    _mat_var_dropdown: QComboBox
    _geo_var_dropdown: QComboBox
    _material_layer_dropdown: QComboBox

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

        self._setup_publish_ui()

    def _setup_publish_ui(self) -> None:
        asset = self._curr_asset
        assert asset is not None

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

        asset_display_name = asset.display_name or asset.name or "Unknown Asset"
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

        mat_items = self._variant_items(asset.material_variants, "default")
        mat_default = "default" if "default" in mat_items else mat_items[0]
        self._mat_var_dropdown = self._build_variant_dropdown(
            label_text="Material Variant:",
            tooltip=(
                "Material variant name used in the publish folder. "
                "Type a new name to create a variant."
            ),
            items=mat_items,
            default_value=mat_default,
            editable=True,
            validator=QRegExpValidator(QRegExp("[a-z][a-z_\\d]*")),
        )

        geo_items = sorted(v for v in asset.geometry_variants if v) or ["main"]
        geo_default = "main" if "main" in geo_items else geo_items[0]
        self._geo_var_dropdown = self._build_variant_dropdown(
            label_text="Geometry Variant:",
            tooltip=("Geometry variant to match the published model."),
            items=geo_items,
            default_value=geo_default,
            editable=False,
        )

        material_layer_items = self._variant_items(asset.material_layers, "default")
        material_layer_default = (
            "default" if "default" in material_layer_items else material_layer_items[0]
        )
        self._material_layer_dropdown = self._build_variant_dropdown(
            label_text="Material Layer:",
            tooltip=("Material layer name used for layered materials."),
            items=material_layer_items,
            default_value=material_layer_default,
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
    def _variant_items(options: typing.Iterable[str], default_value: str) -> list[str]:
        items = sorted({option for option in options if option})
        if items:
            return items
        return [default_value]

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
    def material_layer(self) -> str:
        return self._material_layer_dropdown.currentText()

    def do_export(self, isBatch: bool = False) -> None:
        if not self._curr_asset:
            return
        if not self._preflight():
            return
        if not self._ensure_project_saved():
            return

        mat_var = self.mat_var.strip() or "default"
        geo_var = self.geo_var.strip() or "main"
        material_layer = self.material_layer.strip() or "default"

        asset_label = (
            self._curr_asset.display_name or self._curr_asset.name or "Unknown Asset"
        )
        log.info(
            "Publishing textures for %s (geo=%s, mat=%s, material_layer=%s)",
            asset_label,
            geo_var,
            mat_var,
            material_layer,
        )

        asset_updated = False
        if mat_var not in self._curr_asset.material_variants:
            self._curr_asset.material_variants.add(mat_var)
            log.info("Updating new material variant: %s", mat_var)
            asset_updated = True

        if material_layer not in self._curr_asset.material_layers:
            self._curr_asset.material_layers.add(material_layer)
            log.info("Updating new material layer: %s", material_layer)
            asset_updated = True

        if asset_updated:
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
        log.info("Exporting %d texture sets", len(export_settings))

        if exporter.export(
            export_settings,
            mat_var,
            geo_var,
            material_layer,
        ):
            backup_status = None
            project_path = sp.project.file_path() or ""
            if not project_path:
                backup_status = "Backup skipped: project has no file path."
                log.warning("Backup skipped: project has no file path.")
            else:
                asset_paths = paths_for_asset(self._curr_asset)
                publish_path = asset_paths.publish_textures_layer_dir(
                    geo_var, mat_var, material_layer
                )
                result = backup_if_changed(
                    source_path=Path(project_path),
                    backup_dir=asset_paths.backup_dir,
                    manifest_path=asset_paths.manifest_path,
                    dcc=DCC_SUBSTANCE,
                    publish_path=publish_path,
                    extra={
                        "geo": geo_var,
                        "material": mat_var,
                        "material_layer": material_layer,
                    },
                    asset_name=self._curr_asset.display_name or self._curr_asset.name,
                    asset_path=self._curr_asset.asset_path,
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

            houdini_status: str | None = None
            try:
                houdini_result = self._run_houdini_asset_builder(geo_variant=geo_var)
                houdini_status = self._summarize_houdini_result(houdini_result)
            except HoudiniPublishError as exc:
                houdini_status = f"Houdini publish failed: {exc}"
                log.error("Headless Houdini publish failed from Substance: %s", exc)

            message = "Textures successfully exported!"
            if backup_status:
                message = f"{message}\n{backup_status}"
            if houdini_status:
                message = f"{message}\n{houdini_status}"
            MessageDialog(
                get_main_qt_window(),
                message,
            ).exec_()
        else:
            log.error("Texture export failed for %s", asset_label)
            MessageDialog(
                get_main_qt_window(),
                (
                    "An error occured while exporting textures. Please check the "
                    "console for more information"
                ),
            ).exec_()

    def _run_houdini_asset_builder(self, *, geo_variant: str) -> dict[str, Any]:
        asset = self._curr_asset
        assert asset is not None

        if not Executables.hython.exists():
            raise HoudiniPublishError(
                f"Houdini executable not found at {Executables.hython}"
            )

        asset_paths = paths_for_asset(asset)
        asset_name = asset.name or asset.display_name or asset_paths.root.name
        command = [
            str(Executables.hython),
            "-m",
            "pipe.h.assetbuilder",
            "--asset-root",
            str(asset_paths.root),
            "--asset-name",
            asset_name,
            "--variant",
            geo_variant,
            "--ensure-builder",
            "--publish",
            "--respect-existing",
        ]

        if asset.asset_path:
            command.extend(["--asset-path", asset.asset_path])
        if asset.id is not None:
            command.extend(["--asset-id", str(asset.id)])

        dcc = HoudiniDCC(is_python_shell=True)
        env = dcc._get_env_vars()
        env["PIPE_LOG_LEVEL"] = str(log.getEffectiveLevel())

        log.info(
            "Running headless Houdini publish from Substance for %s (geo=%s)",
            asset_name,
            geo_variant,
        )
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise HoudiniPublishError(
                "Failed to execute hython; verify Houdini is installed."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout or ""
            payload = self._parse_houdini_result(stdout)
            if payload is not None:
                summary = self._summarize_houdini_errors(payload)
                raise HoudiniPublishError(summary) from exc
            if stdout:
                log.error("Houdini asset builder stdout:\n%s", stdout)
            if exc.stderr:
                log.error("Houdini asset builder stderr:\n%s", exc.stderr)
            raise HoudiniPublishError(
                f"Houdini publish failed with exit code {exc.returncode}"
            ) from exc

        payload = self._parse_houdini_result(completed.stdout or "")
        if payload is None:
            log.error("Houdini asset builder stdout:\n%s", completed.stdout or "")
            log.error("Houdini asset builder stderr:\n%s", completed.stderr or "")
            raise HoudiniPublishError(
                "Failed to parse structured output from Houdini publish."
            )

        if payload.get("status") != "success":
            raise HoudiniPublishError(self._summarize_houdini_errors(payload))
        return payload

    @staticmethod
    def _parse_houdini_result(stdout: str) -> dict[str, Any] | None:
        start = stdout.find(_HOUDINI_RESULT_START)
        end = stdout.find(_HOUDINI_RESULT_END)
        if start == -1 or end == -1:
            return None
        json_text = stdout[start + len(_HOUDINI_RESULT_START) : end]
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _summarize_houdini_errors(payload: dict[str, Any]) -> str:
        errors = payload.get("errors", [])
        if isinstance(errors, list):
            messages = [
                str(entry.get("message", ""))
                for entry in errors
                if isinstance(entry, dict) and entry.get("message")
            ]
            if messages:
                return "; ".join(messages)
        publish_payload = payload.get("publish")
        if isinstance(publish_payload, dict):
            publish_errors = publish_payload.get("errors", [])
            if isinstance(publish_errors, list):
                messages = [
                    str(entry.get("message", ""))
                    for entry in publish_errors
                    if isinstance(entry, dict) and entry.get("message")
                ]
                if messages:
                    return "; ".join(messages)
        return "Unknown Houdini publish error."

    @staticmethod
    def _summarize_houdini_result(payload: dict[str, Any]) -> str:
        status = str(payload.get("status", "unknown")).capitalize()
        parts = [f"Houdini publish: {status}"]

        summary = payload.get("summary")
        if isinstance(summary, dict):
            if summary.get("builder_created"):
                parts.append("builder created")
            else:
                parts.append("builder reused")

        publish_payload = payload.get("publish")
        if isinstance(publish_payload, dict):
            export = publish_payload.get("export")
            if isinstance(export, dict):
                export_path = str(export.get("export_path", "")).strip()
                if export_path:
                    parts.append(f"exported {Path(export_path).name}")

            gallery = publish_payload.get("gallery")
            if isinstance(gallery, dict):
                gallery_status = str(gallery.get("status", "")).strip()
                if gallery_status:
                    parts.append(f"gallery {gallery_status}")

            warnings = publish_payload.get("warnings", [])
            if isinstance(warnings, list) and warnings:
                parts.append(f"{len(warnings)} publish warning(s)")

        warnings = payload.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            parts.append(f"{len(warnings)} warning(s)")

        return ", ".join(parts)

    def _ensure_project_saved(self) -> bool:
        if not sp.project.is_open():
            MessageDialog(
                get_main_qt_window(),
                "No Substance Painter project is open.",
                "Publish Textures",
            ).exec_()
            return False

        if sp.project.is_busy():
            MessageDialog(
                get_main_qt_window(),
                "Substance Painter is busy. Wait for the current operation to finish "
                "before publishing.",
                "Painter Busy",
            ).exec_()
            return False

        try:
            if not sp.project.is_in_edition_state():
                MessageDialog(
                    get_main_qt_window(),
                    "The project is still loading. Wait for the project to finish "
                    "loading before publishing.",
                    "Project Loading",
                ).exec_()
                return False
        except Exception:
            log.exception("Failed to query project edition state before publish.")
            return False

        project_path = sp.project.file_path() or ""
        if not project_path:
            MessageDialog(
                get_main_qt_window(),
                "This project has no file path yet. Use Save As before publishing.",
                "Save Required",
            ).exec_()
            return False

        if sp.project.needs_saving():
            dialog = MessageDialogCustomButtons(
                get_main_qt_window(),
                "The project has unsaved changes. Save before publishing?",
                "Save Required",
                has_cancel_button=True,
                ok_name="Save",
                cancel_name="Cancel",
            )
            if not dialog.exec_():
                return False
            try:
                sp.project.save()
            except Exception:
                MessageDialog(
                    get_main_qt_window(),
                    "Failed to save the project. Resolve any file issues and try again.",
                    "Save Failed",
                ).exec_()
                log.exception(
                    "Failed to save Substance Painter project before publish."
                )
                return False

            if sp.project.needs_saving():
                MessageDialog(
                    get_main_qt_window(),
                    "The project still appears unsaved. Please save manually before publishing.",
                    "Save Required",
                ).exec_()
                return False

        return True


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
    _stack: sp.textureset.Stack | None
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

        self._stack = None
        try:
            self._stack = self._tex_set.get_stack()
        except ValueError:
            MessageDialog(
                get_main_qt_window(),
                (
                    f'Texture Set "{_texture_set_name(self._tex_set)}" uses material '
                    "layering. This publish tool currently supports non-layered "
                    "texture sets only."
                ),
            ).exec_()
            self._setup_unsupported_layout()
            return

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

    def _setup_unsupported_layout(self) -> None:
        layout = QtWidgets.QHBoxLayout()
        self._enabled_checkbox = QtWidgets.QCheckBox()
        self._enabled_checkbox.setChecked(False)
        self._enabled_checkbox.setEnabled(False)
        layout.addWidget(self._enabled_checkbox, 10, QtCore.Qt.AlignTop)

        message = QLabel(
            f"{_texture_set_name(self._tex_set)} "
            "(material layering not supported by this exporter)"
        )
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 11px; color: #8a8a8a;")
        layout.addWidget(message, 90)
        self.setLayout(layout)

    def _setup_ui(self) -> None:
        assert self._stack is not None
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
        self.label = QLabel(_texture_set_name(self._tex_set))
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
        if self._stack is None:
            return False
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
