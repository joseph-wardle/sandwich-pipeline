from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from math import log2
from pathlib import Path
from re import findall
from typing import TYPE_CHECKING

from Qt import QtCore, QtWidgets
from Qt.QtCore import QRegExp
from Qt.QtGui import QIcon, QPixmap, QRegExpValidator
from Qt.QtWidgets import (
    QComboBox,
    QDialog,
    QLabel,
    QLayout,
    QMainWindow,
    QProgressBar,
)

if TYPE_CHECKING:
    import typing

import substance_painter as sp
from env_sg import DB_Config
from substance_painter.exception import ProjectError, ServiceNotFoundError

from pipe.asset.paths import paths_for_asset
from pipe.asset.version_adapter import asset_owner_for, substance_project_stream
from pipe.db import DB
from pipe.glui.dialogs import ButtonPair, MessageDialog, MessageDialogCustomButtons
from pipe.sp.export import Exporter, TexSetExportSettings
from pipe.sp.houdini import HoudiniPublishError, run_asset_builder, summarize_result
from pipe.sp.local import get_main_qt_window
from pipe.sp.metadata import get_active_asset_from_project
from pipe.sp.util import docs_link_html, texture_set_name
from pipe.sp.progress import (
    DEFAULT_PUBLISH_STAGE_SEQUENCE,
    PublishProgressUpdate,
    PublishStage,
)
from pipe.struct.db import Asset
from pipe.struct.material import DisplacementSource, NormalSource, NormalType
from pipe.util import checkbox_callback_helper, dict_index
from pipe.versioning.store import backup_if_changed

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PendingPublishRequest:
    asset_label: str
    export_settings: tuple[TexSetExportSettings, ...]
    geo_var: str
    mat_var: str
    material_layer: str
    save_required: bool
    stage_sequence: tuple[PublishStage, ...]
    version_title: str
    version_note: str | None


@dataclass
class _ActivePublishContext:
    request: _PendingPublishRequest
    progress_dialog: "_PublishProgressDialog"


class _PublishProgressDialog(QDialog):
    _allow_close: bool
    _detail_label: QLabel
    _progress_bar: QProgressBar
    _stage_label: QLabel
    _stage_sequence: tuple[PublishStage, ...]
    _step_label: QLabel

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        stage_sequence: typing.Sequence[PublishStage],
    ) -> None:
        super().__init__(parent)
        self._allow_close = False
        self._stage_sequence = tuple(stage_sequence)

        self.setWindowTitle("Publishing Textures")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlags(
            (self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
            | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setWindowModality(QtCore.Qt.WindowModal)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self._step_label = QLabel("")
        self._step_label.setStyleSheet("font-size: 11px; color: #8a8a8a;")
        layout.addWidget(self._step_label)

        self._stage_label = QLabel("Preparing publish")
        self._stage_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(self._stage_label)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setRange(0, 0)
        layout.addWidget(self._progress_bar)

    def event(self, event: QtCore.QEvent) -> bool:
        if not self._allow_close and event.type() == QtCore.QEvent.Close:
            event.ignore()
            return True
        return super().event(event)

    def reject(self) -> None:
        if self._allow_close:
            super().reject()

    def finish(self) -> None:
        if self._allow_close:
            return
        self._allow_close = True
        self.close()

    def update_progress(self, update: PublishProgressUpdate) -> None:
        step_index = self._step_index(update.stage)
        self._step_label.setText(f"Step {step_index} of {len(self._stage_sequence)}")
        self._stage_label.setText(update.stage.label)
        self._detail_label.setText(update.message)

        if update.is_determinate:
            total = max(1, int(update.total or 0))
            current = max(0, min(int(update.current or 0), total))
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._progress_bar.setFormat("%v / %m")
        else:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setFormat("")

        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

        QtWidgets.QApplication.processEvents()

    def _step_index(self, stage: PublishStage) -> int:
        try:
            return self._stage_sequence.index(stage) + 1
        except ValueError:
            return len(self._stage_sequence)


class SubstanceExportWindow(QMainWindow, ButtonPair):
    _active_publish_context: _ActivePublishContext | None
    _curr_asset: Asset | None
    _central_widget: QtWidgets.QWidget
    _conn: DB
    _main_layout: QLayout
    _mat_var_dropdown: QComboBox
    _geo_var_dropdown: QComboBox
    _material_layer_dropdown: QComboBox
    _version_title_field: QtWidgets.QLineEdit
    _version_note_field: QtWidgets.QTextEdit

    # _mat_var_enabled: QtWidgets.QCheckBox
    # _metadataManager: pipe.sp.metadata.MetadataUpdater
    _tex_set_dict: dict[sp.textureset.TextureSet, "TexSetWidget"]

    def __init__(self, flags: QtCore.Qt.WindowFlags | None = None) -> None:
        super(SubstanceExportWindow, self).__init__(get_main_qt_window())

        self._active_publish_context = None
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

    def event(self, event: QtCore.QEvent) -> bool:
        if (
            self._active_publish_context is not None
            and event.type() == QtCore.QEvent.Close
        ):
            event.ignore()
            return True
        return super().event(event)

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

        geo_items = [
            str(v) for v in sorted(v for v in asset.geometry_variants if v)
        ] or ["main"]
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
        self._build_version_metadata_fields()

        self._init_buttons(has_cancel_button=True, ok_name="Export")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)
        ok_btn = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setToolTip("Export textures and convert them to TEX/preview files.")
        cancel_btn = self.buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setToolTip("Close without exporting.")
        self._main_layout.addWidget(self.buttons)
        self._update_export_button_state()

        footer = QLabel(
            "Tip: Make sure your project was opened via Open Asset so the asset "
            "metadata is stored in the project. For more information, see "
            f"{docs_link_html()}."
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

    def _build_version_metadata_fields(self) -> None:
        title_widget = QtWidgets.QWidget()
        title_layout = QtWidgets.QHBoxLayout(title_widget)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)

        title_label = QLabel("Version Title:")
        title_label.setToolTip("Required. This appears in version history.")
        title_layout.addWidget(title_label, 30)

        self._version_title_field = QtWidgets.QLineEdit()
        self._version_title_field.setPlaceholderText("e.g. Dirt pass refinement")
        self._version_title_field.setToolTip(
            "Required. Artists see this title in version history."
        )
        self._version_title_field.textChanged.connect(self._update_export_button_state)
        title_layout.addWidget(self._version_title_field, 70)
        self._main_layout.addWidget(title_widget)

        note_label = QLabel("Version Note (optional):")
        note_label.setToolTip("Optional context shown in version history details.")
        self._main_layout.addWidget(note_label)

        self._version_note_field = QtWidgets.QTextEdit()
        self._version_note_field.setPlaceholderText(
            "Optional details for this texture publish."
        )
        self._version_note_field.setFixedHeight(72)
        self._version_note_field.setToolTip(
            "Optional note shown in version history details."
        )
        self._main_layout.addWidget(self._version_note_field)

    def _update_export_button_state(self) -> None:
        ok_btn = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setEnabled(bool(self.version_title))

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

    @property
    def version_title(self) -> str:
        return self._version_title_field.text().strip()

    @property
    def version_note(self) -> str | None:
        note = self._version_note_field.toPlainText().strip()
        return note or None

    def do_export(self, isBatch: bool = False) -> None:
        if self._active_publish_context is not None:
            return
        if not self._curr_asset:
            return
        if not self._preflight():
            return

        version_title = self.version_title
        if not version_title:
            MessageDialog(
                get_main_qt_window(),
                "Version title is required before exporting textures.",
                "Publish Textures",
            ).exec_()
            return
        if not self._ensure_project_ready():
            return
        save_required = sp.project.needs_saving()
        if save_required and not self._confirm_save_before_publish():
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

        request = _PendingPublishRequest(
            asset_label=asset_label,
            export_settings=tuple(export_settings),
            geo_var=geo_var,
            mat_var=mat_var,
            material_layer=material_layer,
            save_required=save_required,
            stage_sequence=tuple(
                stage
                for stage in DEFAULT_PUBLISH_STAGE_SEQUENCE
                if save_required or stage is not PublishStage.SAVING_PROJECT
            ),
            version_title=version_title,
            version_note=self.version_note,
        )
        self._begin_publish(request)

    def _begin_publish(self, request: _PendingPublishRequest) -> None:
        progress_dialog = _PublishProgressDialog(
            self,
            stage_sequence=request.stage_sequence,
        )
        context = _ActivePublishContext(
            request=request,
            progress_dialog=progress_dialog,
        )
        self._active_publish_context = context
        self._set_publish_controls_enabled(False)

        initial_stage = (
            PublishStage.SAVING_PROJECT
            if request.save_required
            else PublishStage.PREPARING_PUBLISH
        )
        progress_dialog.update_progress(
            PublishProgressUpdate(
                stage=initial_stage,
                message=(
                    "Preparing to save the Substance Painter project and start publish."
                    if request.save_required
                    else "Preparing to start publish."
                ),
            )
        )

        # Let Qt paint the progress dialog before Painter begins synchronous work.
        QtCore.QTimer.singleShot(
            0,
            lambda: self._schedule_publish_when_idle(context),
        )

    def _schedule_publish_when_idle(self, context: _ActivePublishContext) -> None:
        if not self._is_active_publish_context(context):
            return

        request = context.request
        wait_stage = (
            PublishStage.SAVING_PROJECT
            if request.save_required
            else PublishStage.PREPARING_PUBLISH
        )
        context.progress_dialog.update_progress(
            PublishProgressUpdate(
                stage=wait_stage,
                message=(
                    "Waiting for Substance Painter to become idle before saving and publishing."
                    if request.save_required
                    else "Waiting for Substance Painter to become idle before publishing."
                ),
            )
        )

        try:
            sp.project.execute_when_not_busy(lambda: self._run_publish_request(context))
        except (ProjectError, ServiceNotFoundError):
            log.exception("Failed to schedule publish when Substance Painter is idle.")
            self._show_publish_message(
                context,
                "Failed to start the publish in Substance Painter. Try again after the project finishes loading.",
                title="Publish Startup Failed",
            )

    def _run_publish_request(self, context: _ActivePublishContext) -> None:
        if not self._is_active_publish_context(context):
            return
        if not self._curr_asset:
            self._show_publish_message(
                context,
                "The current asset could not be resolved for publish.",
                title="Texture Export Failed",
            )
            return

        request = context.request
        progress_dialog = context.progress_dialog
        exporter = Exporter(self._curr_asset)

        try:
            asset_updated = False
            if request.mat_var not in self._curr_asset.material_variants:
                self._curr_asset.material_variants.add(request.mat_var)
                log.info("Updating new material variant: %s", request.mat_var)
                asset_updated = True

            if request.material_layer not in self._curr_asset.material_layers:
                self._curr_asset.material_layers.add(request.material_layer)
                log.info("Updating new material layer: %s", request.material_layer)
                asset_updated = True

            if asset_updated:
                self._conn.update_asset(self._curr_asset)

            log.info("Exporting!")

            if request.save_required:
                progress_dialog.update_progress(
                    PublishProgressUpdate(
                        stage=PublishStage.SAVING_PROJECT,
                        message="Saving the Substance Painter project before publish.",
                    )
                )
                try:
                    sp.project.save()
                except ProjectError:
                    log.exception(
                        "Failed to save Substance Painter project before publish."
                    )
                    self._show_publish_message(
                        context,
                        "Failed to save the project. Resolve any file issues and try again.",
                        title="Save Failed",
                    )
                    return

                if sp.project.needs_saving():
                    self._show_publish_message(
                        context,
                        "The project still appears unsaved. Please save manually before publishing.",
                        title="Save Required",
                    )
                    return

            progress_dialog.update_progress(
                PublishProgressUpdate(
                    stage=PublishStage.PREPARING_PUBLISH,
                    message="Preparing the publish configuration and enabled texture sets.",
                )
            )

            export_success = exporter.export(
                request.export_settings,
                request.mat_var,
                request.geo_var,
                request.material_layer,
                progress_callback=progress_dialog.update_progress,
            )
            if not export_success:
                log.error("Texture export failed for %s", request.asset_label)
                error_message = exporter.last_error_message or (
                    "An error occurred while exporting textures. Please check the "
                    "console for more information."
                )
                self._show_publish_message(
                    context,
                    error_message,
                    title="Texture Export Failed",
                )
                return

            backup_status = None
            project_path = sp.project.file_path() or ""
            if not project_path:
                backup_status = "Backup skipped: project has no file path."
                log.warning("Backup skipped: project has no file path.")
            else:
                progress_dialog.update_progress(
                    PublishProgressUpdate(
                        stage=PublishStage.BACKING_UP_PROJECT,
                        message="Saving a versioned backup of the Substance Painter project.",
                    )
                )
                asset_paths = paths_for_asset(self._curr_asset)
                publish_path = asset_paths.publish_textures_layer_dir(
                    request.geo_var,
                    request.mat_var,
                    request.material_layer,
                )
                project_stream = substance_project_stream(
                    asset_paths,
                    request.geo_var,
                    owner=asset_owner_for(self._curr_asset),
                )
                result = backup_if_changed(
                    source_path=Path(project_path),
                    backup_dir=project_stream.backup_dir,
                    manifest_path=project_stream.manifest_path,
                    dcc=project_stream.dcc,
                    stream_key=project_stream.stream_key,
                    stem=project_stream.stem,
                    ext=project_stream.ext,
                    stream_label=project_stream.label,
                    working_path=project_stream.working_path,
                    title=request.version_title,
                    publish_path=publish_path,
                    context="publish",
                    note=request.version_note,
                    extra={
                        "geo": request.geo_var,
                        "material": request.mat_var,
                        "material_layer": request.material_layer,
                    },
                    owner=project_stream.owner,
                )

                if result is None:
                    backup_status = "Backup skipped: source file missing."
                    log.warning("Backup skipped: source file missing.")
                elif result.changed:
                    if result.backup_path:
                        version_label = (
                            f"v{int(result.version):03d}"
                            if result.version is not None
                            else result.backup_path.name
                        )
                        backup_status = (
                            f'Backup created: {version_label} "{request.version_title}"'
                        )
                        log.info("Backup created at %s", result.backup_path)
                    else:
                        backup_status = "Backup created."
                        log.info("Backup created for %s", project_path)
                else:
                    backup_status = "Backup skipped: no changes detected."
                    log.info("Backup skipped: no changes detected.")

            houdini_status: str | None = None
            try:
                progress_dialog.update_progress(
                    PublishProgressUpdate(
                        stage=PublishStage.RUNNING_HOUDINI,
                        message="Running the Houdini asset publish step.",
                    )
                )
                houdini_result = run_asset_builder(
                    self._curr_asset, geo_variant=request.geo_var
                )
                houdini_status = summarize_result(houdini_result)
            except HoudiniPublishError as exc:
                houdini_status = f"Houdini publish failed: {exc}"
                log.error("Headless Houdini publish failed from Substance: %s", exc)

            message = "Textures successfully exported!"
            if backup_status:
                message = f"{message}\n{backup_status}"
            if houdini_status:
                message = f"{message}\n{houdini_status}"
            self._show_publish_message(context, message)
        except Exception as exc:
            log.exception(
                "Unexpected error while publishing textures for %s", request.asset_label
            )
            self._show_publish_message(
                context,
                "An unexpected error occurred while publishing textures.\n"
                f"Details: {exc}",
                title="Texture Export Failed",
            )
        finally:
            self._finish_publish_context(context)

    def _is_active_publish_context(self, context: _ActivePublishContext) -> bool:
        return self._active_publish_context is context

    def _finish_publish_context(self, context: _ActivePublishContext) -> None:
        if self._active_publish_context is not context:
            return
        self._active_publish_context = None
        context.progress_dialog.finish()
        self._set_publish_controls_enabled(True)

    def _set_publish_controls_enabled(self, enabled: bool) -> None:
        self._central_widget.setEnabled(enabled)
        self.buttons.setEnabled(enabled)

    def _show_publish_message(
        self,
        context: _ActivePublishContext,
        message: str,
        *,
        title: str | None = None,
    ) -> None:
        self._finish_publish_context(context)
        MessageDialog(
            get_main_qt_window(),
            message,
            title or "Publish Textures",
        ).exec_()

    def _ensure_project_ready(self) -> bool:
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
        except ServiceNotFoundError:
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

        return True

    def _confirm_save_before_publish(self) -> bool:
        dialog = MessageDialogCustomButtons(
            get_main_qt_window(),
            "The project has unsaved changes. Save before publishing?",
            "Save Required",
            has_cancel_button=True,
            ok_name="Save",
            cancel_name="Cancel",
        )
        return bool(dialog.exec_())


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
                    f'Texture Set "{texture_set_name(self._tex_set)}" uses material '
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
            f"{texture_set_name(self._tex_set)} "
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
        self.label = QLabel(texture_set_name(self._tex_set))
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
