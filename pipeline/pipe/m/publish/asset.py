from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence, cast

import maya.cmds as mc
from env import Executables
from Qt.QtCore import QRegExp
from Qt.QtGui import QRegExpValidator, QTextCursor
from Qt.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget
from shared.util import get_pipe_path
from software.houdini.dcc import HoudiniDCC

from pipe.asset.paths import DCC_MAYA, paths_for_asset
from pipe.asset.versioning import BackupResult, backup_if_changed
from pipe.db import DB
from pipe.glui.dialogs import (
    FilteredListDialog,
    MessageDialog,
    MessageDialogCustomButtons,
)
from pipe.m.assetfile import (
    read_asset_metadata,
    resolve_asset_from_scene_path,
    write_asset_metadata,
)
from pipe.m.util import maintain_selection
from pipe.struct.db import Asset, SGEntity

if TYPE_CHECKING:
    pass

from .publisher import Publisher

try:
    from modelChecker.modelChecker_UI import UI as MCUI
except TypeError:
    # this external code throws errors when in headless mode
    MCUI = object

log = logging.getLogger(__name__)
ASSET_BUILDER_SCRIPT = get_pipe_path() / "pipe/h/assetbuilder.py"
# Temporary switch: disable Houdini component builds until HDAs/tooling are ready.
ENABLE_HOUDINI_ASSET_BUILD = False


class HoudiniBuildError(RuntimeError):
    """Raised when the Houdini asset build fails"""

    pass


class _PublishAssetVariantControls:
    _geo_var_dropdown: QComboBox
    _conn: Optional[DB]

    def _init_variant_controls(self) -> None:
        geo_var_widget = QWidget(self)  # type: ignore[misc]
        geo_var_layout = QHBoxLayout(geo_var_widget)
        geo_var_layout.setContentsMargins(0, 0, 0, 0)
        geo_var_layout.setSpacing(0)
        geo_var_settings_widget = QWidget()
        geo_var_settings_layout = QHBoxLayout(geo_var_settings_widget)
        geo_var_label = QLabel("Geometry Variant:")
        geo_var_settings_layout.addWidget(geo_var_label, 30)

        self._geo_var_dropdown = QComboBox()
        self._geo_var_dropdown.setEditable(True)
        self._geo_var_dropdown.setCurrentText("default")
        self._geo_var_dropdown.setToolTip(
            "Enter or select the geometry variant to publish."
        )
        pattern = QRegExp("[a-z][a-z_\d]*")
        geo_var_validator = QRegExpValidator(pattern)
        self._geo_var_dropdown.setValidator(geo_var_validator)
        geo_var_settings_layout.addWidget(self._geo_var_dropdown, 70)
        geo_var_layout.addWidget(geo_var_settings_widget, 90)
        self._layout.addWidget(geo_var_widget)  # type: ignore[attr-defined]

    def get_selected_variant(self) -> str:
        return self._geo_var_dropdown.currentText()

    def _populate_geo_var(self, asset: Asset | None) -> None:
        if asset and hasattr(asset, "geometry_variants"):
            variants = sorted(set(asset.geometry_variants) | {"main"})
        else:
            variants = []
        self._geo_var_dropdown.clear()
        self._geo_var_dropdown.addItems(variants)


class PublishAssetOptionsDialog(FilteredListDialog, _PublishAssetVariantControls):
    """Publish dialog for the current scene asset (read-only asset name)."""

    _selected_asset_name: Optional[str]

    def __init__(
        self, parent: QWidget | None, items: Sequence[str], conn: Optional[DB]
    ) -> None:
        super().__init__(
            parent,
            items,
            "Publish Asset",
            "Asset to publish",
            accept_button_name="Publish",
        )
        self._conn = conn
        self._selected_asset_name = items[0] if items else None

        self.resize(460, 240)

        if hasattr(self, "_filter_field"):
            self._filter_field.setVisible(False)
        self._list_widget.setVisible(False)
        if hasattr(self, "_list_label"):
            self._list_label.setVisible(False)

        asset_label = QLabel(
            f"Asset: {self._selected_asset_name or 'Unknown'}",
            parent=self,
        )
        asset_label.setToolTip("Asset resolved from the current scene metadata.")
        self._layout.insertWidget(0, asset_label)

        self._init_variant_controls()

        self._layout.addStretch(1)
        asset = None
        if self._conn and self._selected_asset_name:
            asset = self._conn.get_asset_by_display_name(self._selected_asset_name)
        self._populate_geo_var(asset)

    def get_selected_item(self) -> str | None:
        return self._selected_asset_name

    def _on_item_selected(self) -> None:
        return


class PublishAssetPickerDialog(FilteredListDialog, _PublishAssetVariantControls):
    """Fallback dialog that lets users choose the asset to publish."""

    def __init__(
        self, parent: QWidget | None, items: Sequence[str], conn: Optional[DB]
    ) -> None:
        super().__init__(
            parent,
            items,
            "Publish Asset",
            "Select asset to publish",
            accept_button_name="Publish",
        )
        self._conn = conn
        self.resize(520, 520)
        self._init_variant_controls()
        self._populate_geo_var(None)
        self._layout.addStretch(1)

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if self._conn and selected:
            asset = self._conn.get_asset_by_display_name(selected)
        else:
            return
        self._populate_geo_var(asset)


class AssetPublisher(Publisher):
    _override: bool
    _geo_variant: str
    _component_export_dir: Path | None
    _component_hip_path: Path | None
    _component_basename: str | None
    _asset_name: str | None
    _houdini_result: dict[str, Any] | None

    def __init__(self) -> None:
        super().__init__(PublishAssetOptionsDialog)
        self._geo_variant = "main"
        self._component_export_dir = None
        self._component_hip_path = None
        self._component_basename = None
        self._asset_name = None
        self._houdini_result = None
        self._scene_asset: Asset | None = None
        self._backup_result: BackupResult | None = None
        self._backup_status: str | None = None

    def _resolve_scene_asset(self) -> Asset | None:
        metadata = read_asset_metadata(self._conn)
        if metadata.asset:
            return metadata.asset

        scene_path = mc.file(query=True, sn=True) or ""
        if not scene_path:
            log.warning("No scene path; cannot resolve asset metadata.")
            return None

        asset = resolve_asset_from_scene_path(self._conn, Path(scene_path))
        if asset:
            log.info("Resolved asset from scene path; writing file metadata.")
            write_asset_metadata(asset)
        else:
            log.warning("Failed to resolve asset from scene path: %s", scene_path)
        return asset

    def _ensure_scene_saved(self) -> bool:
        scene_path = mc.file(query=True, sn=True) or ""
        if not scene_path:
            MessageDialog(
                self._window,
                "Scene must be saved before publishing. Please save the asset file and try again.",
                "Save Required",
            ).exec_()
            log.warning("Publish canceled: scene has no file path.")
            return False

        if not mc.file(query=True, modified=True):
            return True

        response = mc.confirmDialog(
            title="Save Changes",
            message="This scene has unsaved changes. Save before publishing?",
            button=["Save", "Cancel"],
            defaultButton="Save",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if response != "Save":
            log.info("Publish canceled: user declined to save scene.")
            return False

        try:
            mc.file(save=True, force=True)
        except Exception:
            MessageDialog(
                self._window,
                "Failed to save the current scene. Please resolve any file issues and try again.",
                "Save Failed",
            ).exec_()
            log.exception("Failed to save scene before publish.")
            return False

        return True

    def _run_backup(self, asset: Asset) -> None:
        self._backup_result = None
        self._backup_status = None

        scene_path = mc.file(query=True, sn=True) or ""
        if not scene_path:
            self._backup_status = "Backup skipped: scene has no file path."
            log.warning("Backup skipped: scene has no file path.")
            return

        asset_paths = paths_for_asset(asset)
        result = backup_if_changed(
            source_path=Path(scene_path),
            backup_dir=asset_paths.backup_dir,
            manifest_path=asset_paths.manifest_path,
            dcc=DCC_MAYA,
            variant=self._geo_variant,
            publish_path=self._publish_path,
            asset_name=asset.name,
            asset_path=asset.path,
            asset_id=asset.id,
        )

        if result is None:
            self._backup_status = "Backup skipped: source file missing."
            log.warning("Backup skipped: source file missing.")
            return

        self._backup_result = result
        if result.changed:
            if result.backup_path:
                self._backup_status = f"Backup created: {result.backup_path.name}"
                log.info("Backup created at %s", result.backup_path)
            else:
                self._backup_status = "Backup created."
                log.info("Backup created for %s", scene_path)
        else:
            self._backup_status = "Backup skipped: no changes detected."
            log.info("Backup skipped: no changes detected.")

    def _configure_dialog_for_scene(self) -> None:
        self._scene_asset = self._resolve_scene_asset()
        if self._scene_asset:
            self._dialog_T = PublishAssetOptionsDialog
            return

        self._dialog_T = PublishAssetPickerDialog
        MessageDialog(
            self._window,
            "Asset metadata is missing from this scene. "
            "Please select the asset to publish.",
            "Asset Selection Required",
        ).exec_()
        log.warning("Asset metadata missing; falling back to asset picker dialog.")

    @staticmethod
    def _compute_component_basename(asset: Asset, variant: str) -> tuple[str, str]:
        name = (asset.name or "").strip()
        if not name or name == "none":
            name = ""
        if not name and asset.path:
            name = Path(asset.path).name
        if not name:
            name = "asset"

        base_name = name
        if variant and variant != "main":
            base_name = f"{base_name}_{variant}"
        return name, base_name

    def _prepublish(self) -> bool:
        checker = ModelChecker.get()
        self._override = False
        if not checker.check_selected():
            checker_fail_dialog = MessageDialogCustomButtons(
                self._window,
                "Error. This asset did not pass the model checker. Please "
                "ensure your model meets the requirements set by the model "
                "checker.",
                "Cannot export: Model Checker",
                has_cancel_button=True,
                ok_name="Override",
                cancel_name="Ok",
            )
            self._override = bool(checker_fail_dialog.exec_())
            if not self._override:
                cursor = QTextCursor(checker.reportOutputUI.textCursor())
                cursor.setPosition(0)
                cursor.insertHtml(
                    "<h1>Asset not exported. Please resolve model checks.</h1>"
                )
                return False
        self._configure_dialog_for_scene()
        return True

    def _get_entity_list(self) -> list[str]:
        if self._scene_asset:
            return [self._scene_asset.display_name]
        return self._conn.get_asset_display_name_list(sorted=True)

    def _get_entity_from_name(self, display_name: str) -> SGEntity | None:
        return self._conn.get_asset_by_display_name(display_name)

    def _get_asset(self) -> Asset | None:
        """Get the asset from the database."""
        dialog = cast(PublishAssetOptionsDialog, self._dialog)
        asset_display_name = dialog.get_selected_item()
        if not asset_display_name:
            return None
        return self._conn.get_asset_by_display_name(asset_display_name)

    def _get_variant_name(self) -> str | None:
        """Get the variant name from the dialog."""
        dialog = cast(PublishAssetOptionsDialog, self._dialog)
        return dialog.get_selected_variant()

    def _get_save_path(self) -> Path | None:
        asset = self._get_asset()
        if not asset:
            MessageDialog(
                self._window,
                "Error: No asset selected. Nothing exported.",
                "Error",
            ).exec_()
            return None

        variant_name = self._get_variant_name()
        if not variant_name:
            MessageDialog(
                self._window,
                "Error: No variant selected. Nothing exported.",
                "Error",
            ).exec_()
            return None

        if not asset.path:
            MessageDialog(
                self._window,
                "Error: No path for this Asset set in ShotGrid. Nothing exported",
                "Error",
            ).exec_()
            return None

        write_asset_metadata(asset)

        self._geo_variant = variant_name

        if variant_name not in asset.geometry_variants:
            asset.geometry_variants.add(variant_name)
            log.info(f"Updating new geo variant: {variant_name}")
            self._conn.update_asset(asset)

        name, basename = self._compute_component_basename(asset, variant_name)
        asset_paths = paths_for_asset(asset)
        self._asset_name = name
        self._component_basename = basename
        return asset_paths.publish_source_variant_usd(variant_name)

    def _get_confirm_message(self) -> str:
        message = super()._get_confirm_message()

        details: list[str] = []
        if self._backup_status:
            details.append(self._backup_status)

        if self._houdini_result is None:
            details.append("Houdini component publish failed or was skipped.")
        else:
            result = self._houdini_result
            status = result.get("status", "unknown").capitalize()
            mode = result.get("mode", "unknown")

            details.append(f"Houdini build status: {status} ({mode} mode)")
            if result.get("changed_usd_reference"):
                details.append("- Updated USD reference.")
            if result.get("export_performed"):
                details.append(f"- Exported to: {result.get('export_dir')}")

            warnings = result.get("warnings")
            if warnings:
                details.append("")
                details.append("Warnings:")
                details.extend(f"- {w}" for w in warnings)

            errors = result.get("errors")
            if errors:
                details.append("")
                details.append("Errors:")
                details.extend(f"- {e.get('code')}: {e.get('message')}" for e in errors)

        if details:
            return f"{message}\n\n" + "\n".join(details)
        return message

    def publish(self):
        with maintain_selection():
            self._backup_result = None
            self._backup_status = None
            if not self._ensure_scene_saved():
                return

            if not self._prepublish():
                return

            if entity_list := self._get_entity_list():
                if self._dialog_T in (
                    PublishAssetOptionsDialog,
                    PublishAssetPickerDialog,
                ):
                    self._dialog = self._dialog_T(self._window, entity_list, self._conn)
                else:
                    self._dialog = self._dialog_T(self._window, entity_list)

                if not self._dialog.exec_():
                    return

                self._selected_item = self._dialog.get_selected_item()
                if self._selected_item is None:
                    MessageDialog(
                        self._window,
                        "Error: Nothing selected. Nothing exported",
                        "Error",
                    ).exec_()
                    return

                if self._use_sg_entity:
                    try:
                        self._entity = self._get_entity_from_name(self._selected_item)
                    except AssertionError:
                        entity_label = Asset.__name__
                        MessageDialog(
                            self._window,
                            "Error: The selected item did not correspond to a valid "
                            f"{entity_label} in ShotGrid. Please "
                            "report this error. Nothing exported",
                            "Error",
                        ).exec_()
                        return

            self._publish_path = self._get_save_path()
            if not self._publish_path:
                mc.error("No save path found!")
                return

            if not self._presave():
                return

            self._publish_path.parent.mkdir(parents=True, exist_ok=True)
            temp_publish_path = (
                os.getenv("TEMP", "") + os.pathsep + self._publish_path.name
            )

            kwargs = {
                "file": str(
                    temp_publish_path if self._IS_WINDOWS else self._publish_path
                ),
                "selection": True,
                "stripNamespaces": True,
                **self._get_mayausd_kwargs(),
            }

            try:
                mc.mayaUSDExport(**kwargs)  # type: ignore[attr-defined]
            except Exception:
                print(traceback.format_exc())
                MessageDialog(
                    self._window,
                    "WARNING: Publish failed! Please check the console for more information",
                    "Export Failed",
                ).exec_()
                return

            if self._IS_WINDOWS:
                shutil.move(temp_publish_path, self._publish_path)

            self._postpublish()

            asset = None
            if getattr(self, "_entity", None):
                asset = cast(Asset, self._entity)
            if asset is None:
                asset = self._scene_asset or self._resolve_scene_asset()
            if asset:
                self._run_backup(asset)
            else:
                self._backup_status = "Backup skipped: asset could not be resolved."
                log.warning("Backup skipped: asset could not be resolved.")

            MessageDialog(
                self._window,
                self._get_confirm_message(),
                "Export Complete",
            ).exec_()

    def _presave(self) -> bool:
        return True

    def _postpublish(self) -> None:
        if not ENABLE_HOUDINI_ASSET_BUILD:
            log.info("Skipping Houdini component publish (disabled)")
            self._houdini_result = {
                "status": "skipped",
                "mode": "disabled",
                "hip_path": str(self._component_hip_path or ""),
                "usd_path": str(getattr(self, "_publish_path", "")),
                "export_dir": str(self._component_export_dir or ""),
                "export_performed": False,
                "variant": self._geo_variant,
                "changed_usd_reference": False,
                "warnings": [],
                "errors": [],
            }
            return

        asset = cast(Asset, self._entity)
        try:
            self._run_houdini_asset_builder(asset)
        except HoudiniBuildError as exc:
            log.error("Houdini asset build failed: %s", exc, exc_info=True)
            MessageDialog(
                self._window,
                "Houdini component publish failed. Please review the Script Editor for details.",
                "Houdini Export Failed",
            ).exec_()

    def _run_houdini_asset_builder(self, asset: Asset) -> None:
        publish_path = getattr(self, "_publish_path", None)
        if publish_path is None:
            raise HoudiniBuildError(
                "Publish path is undefined; cannot build Houdini component package."
            )

        publish_path = Path(publish_path)
        component_name = self._component_basename or publish_path.stem
        publish_dir = publish_path.parent
        export_dir = publish_dir / "export"
        hip_path = publish_dir / f"{component_name}.hipnc"

        self._component_export_dir = export_dir
        self._component_hip_path = hip_path

        if not ASSET_BUILDER_SCRIPT.exists():
            raise HoudiniBuildError(
                f"Unable to locate Houdini asset builder script: {ASSET_BUILDER_SCRIPT}"
            )

        if not Executables.hython.exists():
            raise HoudiniBuildError(
                f"Houdini executable not found at {Executables.hython}"
            )

        asset_name = self._asset_name or (asset.name or "").strip()
        if not asset_name and asset.path:
            asset_name = Path(asset.path).name
        if not asset_name:
            asset_name = component_name

        command = [
            str(Executables.hython),
            "-m",
            "pipe.h.assetbuilder",
            "--hip-path",
            str(hip_path),
            "--usd-path",
            str(publish_path),
            "--export-dir",
            str(export_dir),
            "--component-name",
            component_name,
            "--asset-name",
            asset_name,
            "--variant",
            self._geo_variant,
        ]

        if asset_name and asset_name != component_name:
            command.extend(["--root-prim", asset_name])

        dcc = HoudiniDCC(is_python_shell=True)
        env = dcc._get_env_vars()
        env["PIPE_LOG_LEVEL"] = str(log.getEffectiveLevel())

        log.info("Running Houdini asset builder for %s", component_name)
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise HoudiniBuildError(
                "Failed to execute hython; verify Houdini is installed."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if stdout:
                log.error("Houdini asset builder stdout:\n%s", stdout)
            if stderr:
                log.error("Houdini asset builder stderr:\n%s", stderr)
            raise HoudiniBuildError(
                f"Houdini component publish failed with exit code {exc.returncode}"
            ) from exc

        # Parse the structured JSON output from the builder script
        stdout = result.stdout or ""
        json_block_start = stdout.find("--BUILD-RESULT--")
        json_block_end = stdout.find("--END-BUILD-RESULT--")

        if json_block_start == -1 or json_block_end == -1:
            log.error("Houdini asset builder stdout:\n%s", stdout)
            log.error("Houdini asset builder stderr:\n%s", result.stderr or "")
            raise HoudiniBuildError(
                "Failed to parse structured output from Houdini build."
            )

        json_text = stdout[json_block_start + len("--BUILD-RESULT--") : json_block_end]
        try:
            self._houdini_result = json.loads(json_text)
        except json.JSONDecodeError:
            log.error("Houdini asset builder stdout:\n%s", stdout)
            raise HoudiniBuildError("Failed to decode JSON from Houdini build.")

        if self._houdini_result.get("status") != "success":  # type: ignore
            errors = self._houdini_result.get("errors", [])  # type: ignore
            error_summary = "; ".join(e.get("message", "Unknown error") for e in errors)
            raise HoudiniBuildError(f"Build failed: {error_summary}")


class ModelChecker(MCUI):
    @classmethod
    def get(cls):
        if not cls.qmwInstance or (type(cls.qmwInstance) is not cls):
            cls.qmwInstance = cls()
        return cls.qmwInstance

    def configure(self) -> None:
        self.uncheckAll()
        commands = [
            # "crossBorder",
            "hardEdges",
            "lamina",
            # "missingUVs",
            "ngons",
            "noneManifoldEdges",
            # "onBorder",
            # "selfPenetratingUVs",
            "zeroAreaFaces",
            "zeroLengthEdges",
        ]
        for cmd in commands:
            self.commandCheckBox[cmd].setChecked(True)

    def check_selected(self) -> bool:
        self.configure()
        self.sanityCheck(["Selection"], True)
        self.createReport("Selection")

        # loop and show UI if anything had an error
        diagnostics = self.contexts["Selection"]["diagnostics"]
        for error in self.commandsList.keys():
            if (error in diagnostics) and len(self.parseErrors(diagnostics[error])):
                self.show_UI()
                return False

        return True

    # Override
    def sanityCheck(self, contextsUuids, refreshSelection=True) -> None:
        """The `sanityCheck` function cannot handle transforms that do not
        have children. This catches those errors and warns the modelers."""
        try:
            super().sanityCheck(contextsUuids, refreshSelection)
        except RuntimeError as err:
            if (
                "(kInvalidParameter): Object is incompatible with this method"
                in err.args
            ):
                MessageDialog(
                    self.parent(),
                    "The model checker could not run. Please ensure that you do "
                    "not have any empty transforms.",
                    "Model Checker Failed",
                ).exec_()
            else:
                raise err
