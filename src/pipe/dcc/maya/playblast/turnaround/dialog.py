"""Render-only Maya turnaround dialog."""

from __future__ import annotations

import logging
from pathlib import Path

import attrs
import maya.cmds as mc
from Qt import QtCore, QtWidgets
from Qt.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast import (
    CUSTOM_FOLDER_ID,
    RENDER_FOLDER_ID,
    AssetEntity,
    Destination,
    DiskDestination,
    FFmpegPreset,
    Playblaster,
    PreviewClip,
    ReviewEntity,
    ScratchEntity,
    ShotGridDestination,
)
from pipe.core.playblast.tempdir import resolve_playblast_tempdir
from pipe.core.playblast.viewer import open_viewer
from pipe.core.shotgrid import normalize_display_name
from pipe.core.ui import ButtonPair, MessageDialog
from pipe.dcc.maya.assetfile import AssetMetadata, read_asset_metadata
from pipe.dcc.maya.playblast.turnaround.config import (
    DEFAULT_FRAMES_PER_PASS,
    Elevation,
    TurnaroundPass,
    TurnaroundPlayblastConfig,
    resolve_turnaround_review_roots,
)
from pipe.dcc.maya.playblast.turnaround.playblaster import MTurnaroundPlayblaster
from pipe.dcc.maya.runtime import get_main_qt_window

log = logging.getLogger(__name__)


def _scene_path() -> Path | None:
    raw_path = mc.file(query=True, sceneName=True)
    if not isinstance(raw_path, str) or not raw_path:
        return None
    return Path(raw_path)


class AssetTurnaroundDialog(ButtonPair, QtWidgets.QMainWindow):
    """Small Maya UI for asset review turnarounds.

    The class-level knobs below are the supported subclassing surface;
    `AnimTurnaroundDialog` overrides them for animation scratch scenes.
    """

    playblaster = MTurnaroundPlayblaster()

    WINDOW_TITLE: str = "SKD Asset Turnaround"
    SUBTITLE: str = "Capture a turnaround, then pick destinations in the viewer"
    SOURCE_VALUE: str = "Pipeline Asset File"
    SOURCE_TOOLTIP: str = "Uses the current Maya asset scene and current selection."
    SUBJECT_LABEL: str = "Asset"
    # Burn the asset name and point count into the HUD (model-review info).
    HUD_ASSET_DETAILS: bool = True
    ELEVATIONS: tuple[Elevation, ...] = tuple(Elevation)
    WIREFRAME_PASSES: bool = True
    DEFAULT_UI_PASSES: tuple[TurnaroundPass, ...] = (
        TurnaroundPass(Elevation.THREE_QUARTER, False),
        TurnaroundPass(Elevation.THREE_QUARTER, True),
    )

    # Key for the viewer's per-tool destination-toggle memory.
    SETTINGS_KEY: str = "maya_asset_turnaround"

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.WINDOW_TITLE)
        self._asset_metadata = self._read_asset_metadata()
        self._review_roots = resolve_turnaround_review_roots()

        self._setup_ui()
        self._update_ui_state()

    # ------------------------------------------------------------------
    # UI assembly
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)

        self._main_layout = QVBoxLayout()
        self._central_widget.setLayout(self._main_layout)

        self._build_header_section()
        self._build_source_section()
        self._build_passes_section()
        self._build_validation_label()
        self._build_buttons()

    def _build_header_section(self) -> None:
        title = QLabel(self.windowTitle())
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel(self.SUBTITLE)
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "The turnaround opens in the viewer; nothing is saved or uploaded "
            "until you confirm destinations there."
        )

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_source_section(self) -> None:
        source_group = QGroupBox("Source")
        source_layout = QGridLayout(source_group)

        source_layout.addWidget(QLabel("Source"), 0, 0)
        source_value = QLabel(self.SOURCE_VALUE)
        source_value.setToolTip(self.SOURCE_TOOLTIP)
        source_layout.addWidget(source_value, 0, 1, 1, 2)

        source_layout.addWidget(QLabel(self.SUBJECT_LABEL), 1, 0)
        self._asset_value = QLabel("-")
        self._asset_value.setToolTip(
            f"Resolved {self.SUBJECT_LABEL.lower()} display name."
        )
        source_layout.addWidget(self._asset_value, 1, 1, 1, 2)

        source_layout.addWidget(QLabel("Review Root"), 2, 0)
        self._review_root_value = QLabel("-")
        self._review_root_value.setToolTip(
            "Uses the current selection when available, otherwise falls back to visible geometry."
        )
        source_layout.addWidget(self._review_root_value, 2, 1)

        self._refresh_selection_button = QPushButton("Refresh Selection")
        self._refresh_selection_button.setToolTip(
            "Refresh the turnaround review roots from the current Maya selection."
        )
        self._refresh_selection_button.clicked.connect(
            self._on_refresh_selection_clicked
        )
        source_layout.addWidget(self._refresh_selection_button, 2, 2)

        source_layout.addWidget(QLabel("Summary"), 3, 0)
        self._passes_value = QLabel("-")
        self._passes_value.setToolTip("Number of selected passes and total runtime.")
        source_layout.addWidget(self._passes_value, 3, 1, 1, 2)

        self._main_layout.addWidget(source_group)

    def _pass_modes(self) -> tuple[bool, ...]:
        """Wireframe-on-shaded values offered as pass columns."""
        return (False, True) if self.WIREFRAME_PASSES else (False,)

    def _build_passes_section(self) -> None:
        passes_group = QGroupBox("Passes")
        passes_layout = QGridLayout(passes_group)

        passes_layout.addWidget(QLabel("Elevation"), 0, 0)
        passes_layout.addWidget(QLabel("Shaded"), 0, 1)
        if self.WIREFRAME_PASSES:
            passes_layout.addWidget(QLabel("Wireframe"), 0, 2)

        self._pass_checkboxes: dict[tuple[Elevation, bool], QCheckBox] = {}
        for row, elevation in enumerate(self.ELEVATIONS, start=1):
            passes_layout.addWidget(QLabel(elevation.label), row, 0)
            for column, wireframe_on_shaded in enumerate(self._pass_modes(), start=1):
                checkbox = QCheckBox()
                checkbox.toggled.connect(self._on_settings_changed)
                self._pass_checkboxes[(elevation, wireframe_on_shaded)] = checkbox
                passes_layout.addWidget(checkbox, row, column)

        for default_pass in self.DEFAULT_UI_PASSES:
            key = (default_pass.elevation, default_pass.wireframe_on_shaded)
            self._pass_checkboxes[key].setChecked(True)
        self._main_layout.addWidget(passes_group)

    def _build_validation_label(self) -> None:
        self._validation_label = QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setVisible(False)
        self._main_layout.addWidget(self._validation_label)

    def _build_buttons(self) -> None:
        self._init_buttons(has_cancel_button=True, ok_name="Create Turnaround")
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setToolTip("Render the turnaround and open it in the viewer.")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)
        self._main_layout.addWidget(self.buttons)

    def _selected_passes(self) -> tuple[TurnaroundPass, ...]:
        selected: list[TurnaroundPass] = []
        for elevation in self.ELEVATIONS:
            for wireframe_on_shaded in self._pass_modes():
                if self._pass_checkboxes[(elevation, wireframe_on_shaded)].isChecked():
                    selected.append(TurnaroundPass(elevation, wireframe_on_shaded))
        return tuple(selected)

    # ------------------------------------------------------------------
    # Scene context
    # ------------------------------------------------------------------

    def _read_asset_metadata(self) -> AssetMetadata | None:
        try:
            return read_asset_metadata()
        except Exception:
            log.exception(
                "Could not resolve asset metadata from the current Maya scene."
            )
            return None

    def _pipeline_display_name(self) -> str | None:
        """The resolved pipeline asset's display name, if this scene has one."""
        if self._asset_metadata and self._asset_metadata.asset:
            return self._asset_metadata.asset.display_name
        if self._asset_metadata and self._asset_metadata.display_name:
            return self._asset_metadata.display_name
        return None

    def _asset_display_name(self) -> str:
        """What the artist and the HUD call this turnaround's subject."""
        pipeline_name = self._pipeline_display_name()
        if pipeline_name:
            return pipeline_name
        scene_path = _scene_path()
        if scene_path is not None:
            return scene_path.stem
        return "turnaround"

    def _asset_filename_token(self) -> str:
        if self._asset_metadata and self._asset_metadata.asset:
            return self._asset_metadata.asset.name
        if self._asset_metadata and self._asset_metadata.name:
            return self._asset_metadata.name
        return normalize_display_name(self._asset_display_name()) or "turnaround"

    def _refresh_context(self) -> None:
        self._asset_metadata = self._read_asset_metadata()
        self._review_roots = resolve_turnaround_review_roots()

    def _refresh_context_fields(self) -> None:
        self._asset_value.setText(self._asset_display_name() or "-")
        summary = self._review_roots.summary
        self._review_root_value.setText(
            f"{summary} ({self._review_roots.source_label})"
        )
        self._passes_value.setText(self._pass_summary_text())

    def _pass_summary_text(self) -> str:
        pass_count = len(self._selected_passes())
        if not pass_count:
            return "No passes selected"
        seconds = pass_count * DEFAULT_FRAMES_PER_PASS / Playblaster.fps
        noun = "pass" if pass_count == 1 else "passes"
        return f"{pass_count} {noun} · ~{seconds:.0f}s"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_state(self) -> str | None:
        if not self._review_roots.roots:
            return (
                "Select geometry for the turnaround, or make visible geometry "
                "available in the scene."
            )

        if not self._selected_passes():
            return "Select at least one turnaround pass."

        return None

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is None:
            return

        validation_error = self._validate_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _update_ui_state(self) -> None:
        self._refresh_context_fields()
        self._update_action_state()

    # ------------------------------------------------------------------
    # Routing for the viewer's Confirm panel
    # ------------------------------------------------------------------

    def _clip_folders(self) -> tuple[DiskDestination, ...]:
        rows: list[DiskDestination] = []
        scene_path = _scene_path()
        if scene_path is not None:
            rows.append(
                DiskDestination(
                    id=RENDER_FOLDER_ID,
                    name="Render Folder",
                    directory=scene_path.parent / "render",
                    preset=FFmpegPreset.WEB,
                )
            )
        rows.append(
            DiskDestination(
                id=CUSTOM_FOLDER_ID,
                name="Custom Folder",
                directory=resolve_playblast_tempdir(),
                preset=FFmpegPreset.WEB,
                # An unsaved scene has no Render Folder row; give the artist
                # one checked destination instead of an all-off panel.
                default_on=scene_path is None,
                browsable=True,
            )
        )
        return tuple(rows)

    def _review_entity(self) -> ReviewEntity:
        """What this turnaround's ShotGrid Version attaches to."""
        pipeline_name = self._pipeline_display_name()
        if pipeline_name:
            return AssetEntity(pipeline_name)
        return ScratchEntity(self._asset_display_name())

    def _clip_destinations(self) -> tuple[Destination, ...]:
        """Every row the viewer offers, in the order it shows them."""
        return (
            *self._clip_folders(),
            ShotGridDestination(entity=self._review_entity()),
        )

    def _routed_clip(self, clip: PreviewClip) -> PreviewClip:
        return attrs.evolve(
            clip,
            output_prefix=f"{self._asset_filename_token()}_turnaround",
            settings_key=self.SETTINGS_KEY,
            destinations=self._clip_destinations(),
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _build_config(self) -> TurnaroundPlayblastConfig:
        return TurnaroundPlayblastConfig(
            asset_label=self._asset_display_name(),
            review_roots=self._review_roots.roots,
            hud_asset_details=self.HUD_ASSET_DETAILS,
            passes=self._selected_passes(),
        )

    def do_export(self) -> None:
        """Render the turnaround frames and open the viewer on them. Nothing
        persists here — that happens in the viewer, on Confirm."""
        self._refresh_context()
        self._update_ui_state()

        validation_error = self._validate_state()
        if validation_error:
            MessageDialog(self, validation_error, "Turnaround").exec_()
            return

        try:
            config = self._build_config()
        except Exception as exc:
            log.exception("Turnaround config generation failed")
            MessageDialog(
                self,
                f"Could not generate turnaround settings.\n\n{exc}",
                "Turnaround Error",
            ).exec_()
            return

        try:
            clip = self.playblaster.configure(config).playblast(parent=self)
        except Exception as exc:
            log.exception("Turnaround export failed")
            MessageDialog(
                self,
                f"Turnaround export failed.\n\n{exc}",
                "Turnaround Error",
            ).exec_()
            return

        open_viewer([self._routed_clip(clip)], parent=get_main_qt_window())
        self.close()

    def _on_refresh_selection_clicked(self) -> None:
        self._refresh_context()
        self._update_ui_state()

    def _on_settings_changed(self, *_args) -> None:
        self._update_ui_state()


__all__ = ["AssetTurnaroundDialog"]
