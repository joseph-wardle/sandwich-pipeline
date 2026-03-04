from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence, cast

import maya.cmds as mc
from env import Executables
from Qt.QtCore import QRegExp
from Qt.QtGui import QRegExpValidator, QTextCursor
from Qt.QtWidgets import QComboBox, QDialogButtonBox, QHBoxLayout, QLabel, QWidget
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
ENABLE_HOUDINI_ASSET_BUILD = True
_HOUDINI_RESULT_START = "--BUILD-RESULT--"
_HOUDINI_RESULT_END = "--END-BUILD-RESULT--"
_TELEMETRY_ACTION_ID_ENV = "PIPE_TELEMETRY_ACTION_ID"


def _current_scene_path() -> Path | None:
    raw_path = mc.file(query=True, sceneName=True)
    if isinstance(raw_path, str) and raw_path:
        return Path(raw_path)
    return None


class HoudiniBuildError(RuntimeError):
    """Raised when the Houdini asset build fails"""

    pass


class _PublishAssetVariantControls:
    _geo_var_dropdown: QComboBox
    _conn: Optional[DB]

    def _init_variant_controls(self) -> None:
        geo_var_widget = QWidget(cast(QWidget, self))
        geo_var_layout = QHBoxLayout(geo_var_widget)
        geo_var_layout.setContentsMargins(0, 0, 0, 0)
        geo_var_layout.setSpacing(0)
        geo_var_settings_widget = QWidget()
        geo_var_settings_layout = QHBoxLayout(geo_var_settings_widget)
        geo_var_label = QLabel("Geometry Variant:")
        geo_var_label.setToolTip("Choose the geometry variant to publish.")
        geo_var_settings_layout.addWidget(geo_var_label, 30)

        self._geo_var_dropdown = QComboBox()
        self._geo_var_dropdown.setEditable(True)
        self._geo_var_dropdown.setCurrentText("main")
        self._geo_var_dropdown.setToolTip(
            "Enter or select the geometry variant to publish."
        )
        pattern = QRegExp("[a-z][a-z_\d]*")
        geo_var_validator = QRegExpValidator(pattern)
        self._geo_var_dropdown.setValidator(geo_var_validator)
        geo_var_settings_layout.addWidget(self._geo_var_dropdown, 70)
        geo_var_layout.addWidget(geo_var_settings_widget, 90)
        insert_at = max(self._layout.count() - 1, 0)  # type: ignore[attr-defined]
        self._layout.insertWidget(insert_at, geo_var_widget)  # type: ignore[attr-defined]

    def get_selected_variant(self) -> str:
        return self._geo_var_dropdown.currentText()

    def _populate_geo_var(self, asset: Asset | None) -> None:
        if asset and hasattr(asset, "geometry_variants"):
            variants = sorted(v for v in asset.geometry_variants if v)
        else:
            variants = []
        if not variants:
            variants = ["main"]
        self._geo_var_dropdown.clear()
        self._geo_var_dropdown.addItems(variants)
        self._geo_var_dropdown.setCurrentText(
            "main" if "main" in variants else variants[0]
        )


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

        insert_at = max(self._layout.count() - 1, 0)
        self._layout.insertStretch(insert_at, 1)
        asset = None
        if self._conn and self._selected_asset_name:
            asset = self._conn.get_asset_by_display_name(self._selected_asset_name)
        self._populate_geo_var(asset)

        ok_btn = self.buttons.button(QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setToolTip("Publish the selected geometry variant.")
        cancel_btn = self.buttons.button(QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setToolTip("Cancel publishing and close this window.")

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
        if hasattr(self, "_filter_field"):
            self._filter_field.setToolTip("Type to filter the asset list.")
        self._list_widget.setToolTip("Select the asset to publish.")
        self._init_variant_controls()
        self._populate_geo_var(None)
        insert_at = max(self._layout.count() - 1, 0)
        self._layout.insertStretch(insert_at, 1)

        ok_btn = self.buttons.button(QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setToolTip("Publish the selected asset and variant.")
        cancel_btn = self.buttons.button(QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setToolTip("Cancel publishing and close this window.")

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

        scene_path = _current_scene_path()
        if scene_path is None:
            log.warning("No scene path; cannot resolve asset metadata.")
            return None

        asset = resolve_asset_from_scene_path(self._conn, scene_path)
        if asset:
            log.info("Resolved asset from scene path; writing file metadata.")
            write_asset_metadata(asset)
        else:
            log.warning("Failed to resolve asset from scene path: %s", scene_path)
        return asset

    def _ensure_scene_saved(self) -> bool:
        scene_path = _current_scene_path()
        if scene_path is None:
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

        scene_path = _current_scene_path()
        if scene_path is None:
            self._backup_status = "Backup skipped: scene has no file path."
            log.warning("Backup skipped: scene has no file path.")
            return

        asset_paths = paths_for_asset(asset)
        result = backup_if_changed(
            source_path=scene_path,
            backup_dir=asset_paths.backup_dir,
            manifest_path=asset_paths.manifest_path,
            dcc=DCC_MAYA,
            variant=self._geo_variant,
            publish_path=self._publish_path,
            asset_name=asset.name,
            asset_path=asset.asset_path,
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
        if not name and asset.asset_path:
            name = Path(asset.asset_path).name
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

        if not asset.asset_path:
            MessageDialog(
                self._window,
                "Error: Could not resolve the location for this asset in ShotGrid. Nothing exported",
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
            status = str(result.get("status", "unknown")).capitalize()
            details.append(f"Houdini publish status: {status}")

            summary = result.get("summary")
            if isinstance(summary, dict):
                hip_path = str(summary.get("hip_path", "")).strip()
                if hip_path:
                    details.append(f"- Builder HIP: {hip_path}")
                details.append(
                    "- Builder: "
                    + (
                        "created"
                        if summary.get("builder_created")
                        else "reused existing network"
                    )
                )
                if summary.get("variant_graph_regenerated"):
                    details.append("- Managed variants: regenerated")
                elif summary.get("respected_existing"):
                    details.append("- Managed variants: respected existing graph")

            publish_result = result.get("publish")
            if isinstance(publish_result, dict):
                publish_status = str(
                    publish_result.get("status", "unknown")
                ).capitalize()
                details.append(f"- Component publish: {publish_status}")

                export = publish_result.get("export")
                if isinstance(export, dict):
                    export_path = str(export.get("export_path", "")).strip()
                    if export_path:
                        details.append(f"- Exported to: {export_path}")

                gallery = publish_result.get("gallery")
                if isinstance(gallery, dict):
                    gallery_status = str(gallery.get("status", "")).strip()
                    if gallery_status:
                        details.append(f"- Gallery sync: {gallery_status}")

            def _append_messages(
                heading: str,
                payload: Any,
                target: list[str],
            ) -> None:
                if not payload:
                    return
                lines: list[str] = []
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict):
                            code = str(item.get("code", "")).strip()
                            msg = str(item.get("message", "")).strip()
                            if code and msg:
                                lines.append(f"- {code}: {msg}")
                            elif msg:
                                lines.append(f"- {msg}")
                            else:
                                lines.append(f"- {item}")
                        else:
                            lines.append(f"- {item}")
                else:
                    lines.append(f"- {payload}")
                if lines:
                    target.append("")
                    target.append(heading)
                    target.extend(lines)

            _append_messages("Warnings:", result.get("warnings"), details)
            if isinstance(publish_result, dict):
                _append_messages(
                    "Publish Warnings:",
                    publish_result.get("warnings"),
                    details,
                )
            _append_messages("Errors:", result.get("errors"), details)
            if isinstance(publish_result, dict):
                _append_messages(
                    "Publish Errors:",
                    publish_result.get("errors"),
                    details,
                )

        if details:
            return f"{message}\n\n" + "\n".join(details)
        return message

    def publish(self):
        publish_telemetry = self._new_publish_telemetry_state()
        publish_path: Path | None = None
        try:
            with maintain_selection():
                self._backup_result = None
                self._backup_status = None
                if not self._ensure_scene_saved():
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="precheck",
                        error_message="Scene must be saved before publishing",
                        exception_type="PrepublishFailed",
                        publish_path=publish_path,
                    )
                    return

                if not self._prepublish():
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="precheck",
                        error_message="Publish precheck failed before export",
                        exception_type="PrepublishFailed",
                        publish_path=publish_path,
                    )
                    return

                if entity_list := self._get_entity_list():
                    if self._dialog_T in (
                        PublishAssetOptionsDialog,
                        PublishAssetPickerDialog,
                    ):
                        self._dialog = self._dialog_T(
                            self._window, entity_list, self._conn
                        )
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
                        self._emit_publish_error(
                            publish_telemetry,
                            error_code_name="precheck",
                            error_message="No publish target selected",
                            exception_type="SelectionError",
                            publish_path=publish_path,
                        )
                        return

                    if self._use_sg_entity:
                        try:
                            self._entity = self._get_entity_from_name(
                                self._selected_item
                            )
                        except AssertionError as exc:
                            entity_label = Asset.__name__
                            MessageDialog(
                                self._window,
                                "Error: The selected item did not correspond to a valid "
                                f"{entity_label} in ShotGrid. Please "
                                "report this error. Nothing exported",
                                "Error",
                            ).exec_()
                            self._emit_publish_error(
                                publish_telemetry,
                                error_code_name="precheck",
                                error_message=str(exc)
                                or "Selected item is not a valid SG entity",
                                exception_type=type(exc).__name__,
                                publish_path=publish_path,
                            )
                            return

                self._restart_publish_telemetry_timer(publish_telemetry)
                self._publish_path = self._get_save_path()
                publish_path = self._publish_path
                if not self._publish_path:
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="precheck",
                        error_message="No publish path resolved",
                        exception_type="PathResolutionError",
                        publish_path=publish_path,
                    )
                    mc.error("No save path found!")
                    return

                if not self._presave():
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="precheck",
                        error_message="Publish presave checks failed",
                        exception_type="PresaveFailed",
                        publish_path=publish_path,
                    )
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
                except Exception as exc:
                    print(traceback.format_exc())
                    MessageDialog(
                        self._window,
                        "WARNING: Publish failed! Please check the console for more information",
                        "Export Failed",
                    ).exec_()
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="export",
                        error_message=str(exc),
                        exception_type=type(exc).__name__,
                        publish_path=publish_path,
                    )
                    return

                if self._IS_WINDOWS:
                    try:
                        shutil.move(temp_publish_path, self._publish_path)
                    except Exception as exc:
                        self._emit_publish_error(
                            publish_telemetry,
                            error_code_name="windows_move",
                            error_message=str(exc),
                            exception_type=type(exc).__name__,
                            publish_path=publish_path,
                        )
                        raise

                try:
                    self._postpublish()
                except Exception as exc:
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="copy",
                        error_message=str(exc),
                        exception_type=type(exc).__name__,
                        publish_path=publish_path,
                    )
                    raise

                asset = None
                if getattr(self, "_entity", None):
                    asset = cast(Asset, self._entity)
                if asset is None:
                    asset = self._scene_asset or self._resolve_scene_asset()
                try:
                    if asset:
                        self._run_backup(asset)
                    else:
                        self._backup_status = (
                            "Backup skipped: asset could not be resolved."
                        )
                        log.warning("Backup skipped: asset could not be resolved.")
                except Exception as exc:
                    self._emit_publish_error(
                        publish_telemetry,
                        error_code_name="copy",
                        error_message=str(exc),
                        exception_type=type(exc).__name__,
                        publish_path=publish_path,
                    )
                    raise

                self._emit_publish_success(
                    publish_telemetry,
                    publish_path=publish_path,
                )

                MessageDialog(
                    self._window,
                    self._get_confirm_message(),
                    "Export Complete",
                ).exec_()
        except Exception as exc:
            self._emit_publish_error(
                publish_telemetry,
                error_code_name="precheck",
                error_message=str(exc),
                exception_type=type(exc).__name__,
                publish_path=publish_path,
            )
            raise

    @staticmethod
    def _component_build_mode(*, ensure_builder: bool, publish_requested: bool) -> str:
        if ensure_builder and publish_requested:
            return "ensure_and_publish"
        if publish_requested:
            return "publish_only"
        if ensure_builder:
            return "ensure_only"
        return "none"

    @staticmethod
    def _new_houdini_build_action_id() -> str | None:
        try:
            from pipe.telemetry import new_action_id
        except Exception:
            return None
        return new_action_id()

    def _emit_houdini_component_build_event(
        self,
        *,
        status: str,
        duration_ms: int,
        variant: str,
        ensure_builder: bool,
        publish_requested: bool,
        warnings_count: int,
        errors_count: int,
        action_id: str | None,
        asset_name: str,
        error_code: str | None = None,
        error_message: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        try:
            from pipe.telemetry import STATUS_ERROR, STATUS_SUCCESS, emit, events
        except Exception:
            return

        if status == "success":
            status_value = STATUS_SUCCESS
        else:
            status_value = STATUS_ERROR

        payload = {
            "mode": self._component_build_mode(
                ensure_builder=ensure_builder,
                publish_requested=publish_requested,
            ),
            "variant": str(variant or "main"),
            "warnings_count": max(0, int(warnings_count)),
            "errors_count": max(0, int(errors_count)),
            "ensure_builder": bool(ensure_builder),
            "publish_requested": bool(publish_requested),
        }

        scope = self._get_publish_scope() or {}
        if asset_name.strip():
            scope = dict(scope)
            scope.setdefault("asset", asset_name.strip())

        error_data = None
        if status == "error":
            if not error_code:
                return
            error_data = {
                "code": error_code,
                "message": error_message or "Houdini component build failed",
                "exception_type": exception_type or "RuntimeError",
            }

        emit(
            events.EVENT_BUILD_HOUDINI_COMPONENT,
            status=status_value,
            action_id=action_id,
            payload=payload,
            metrics={"duration_ms": max(0, int(duration_ms))},
            scope=scope or None,
            error=error_data,
        )

    def _presave(self) -> bool:
        return True

    def _postpublish(self) -> None:
        if not ENABLE_HOUDINI_ASSET_BUILD:
            log.info("Skipping Houdini component publish (disabled)")
            self._houdini_result = {
                "status": "skipped",
                "asset_root": "",
                "asset_name": "",
                "variant": self._geo_variant,
                "ensure_builder": False,
                "publish_requested": False,
                "summary": None,
                "publish": None,
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
        ensure_builder_requested = True
        publish_requested = True
        variant = str(self._geo_variant or "main")
        build_action_id = self._new_houdini_build_action_id()
        build_started_at = time.perf_counter()

        build_failed_code = "HOUDINI_BUILD_FAILED"
        try:
            from pipe.telemetry.registry import ERROR_HOUDINI_BUILD_FAILED

            build_failed_code = ERROR_HOUDINI_BUILD_FAILED
        except Exception:
            pass

        asset_name = self._asset_name or (asset.name or "").strip()
        if not asset_name and asset.asset_path:
            asset_name = Path(asset.asset_path).name
        if not asset_name:
            asset_name = "asset"

        def _duration_ms() -> int:
            return max(0, int((time.perf_counter() - build_started_at) * 1000))

        def _emit_prelaunch_error(
            message: str,
            *,
            exception_type: str,
        ) -> None:
            self._emit_houdini_component_build_event(
                status="error",
                duration_ms=_duration_ms(),
                variant=variant,
                ensure_builder=ensure_builder_requested,
                publish_requested=publish_requested,
                warnings_count=0,
                errors_count=1,
                action_id=build_action_id,
                asset_name=asset_name,
                error_code=build_failed_code,
                error_message=message,
                exception_type=exception_type,
            )

        if getattr(self, "_publish_path", None) is None:
            message = "Publish path is undefined; cannot run Houdini publish."
            _emit_prelaunch_error(
                message,
                exception_type=HoudiniBuildError.__name__,
            )
            raise HoudiniBuildError(message)

        if not Executables.hython.exists():
            message = f"Houdini executable not found at {Executables.hython}"
            _emit_prelaunch_error(
                message,
                exception_type=HoudiniBuildError.__name__,
            )
            raise HoudiniBuildError(message)

        asset_paths = paths_for_asset(asset)
        hip_path = asset_paths.asset_builder_path
        self._component_hip_path = hip_path
        self._component_export_dir = asset_paths.publish_dir

        if asset_name == "asset":
            asset_name = asset_paths.root.name

        command = [
            str(Executables.hython),
            "-m",
            "pipe.h.assetbuilder",
            "--asset-root",
            str(asset_paths.root),
            "--asset-name",
            asset_name,
            "--variant",
            variant,
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
        if build_action_id:
            env[_TELEMETRY_ACTION_ID_ENV] = build_action_id

        log.info(
            "Running Houdini headless publish for %s (variant=%s)",
            asset_name,
            self._geo_variant,
        )
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            message = "Failed to execute hython; verify Houdini is installed."
            _emit_prelaunch_error(
                message,
                exception_type=type(exc).__name__,
            )
            raise HoudiniBuildError(message) from exc
        except OSError as exc:
            message = f"Failed to launch hython: {exc}"
            _emit_prelaunch_error(
                message,
                exception_type=type(exc).__name__,
            )
            raise HoudiniBuildError(message) from exc
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            payload = self._parse_houdini_result_payload(stdout)
            if payload is not None:
                self._houdini_result = payload
                message = f"Build failed: {self._summarize_houdini_errors(payload)}"
                raise HoudiniBuildError(message) from exc
            if stdout:
                log.error("Houdini asset builder stdout:\n%s", stdout)
            if stderr:
                log.error("Houdini asset builder stderr:\n%s", stderr)
            message = (
                f"Houdini component publish failed with exit code {exc.returncode}"
            )
            raise HoudiniBuildError(message) from exc

        # Parse the structured JSON output from the builder script
        stdout = result.stdout or ""
        payload = self._parse_houdini_result_payload(stdout)
        if payload is None:
            log.error("Houdini asset builder stdout:\n%s", stdout)
            log.error("Houdini asset builder stderr:\n%s", result.stderr or "")
            message = "Failed to parse structured output from Houdini build."
            raise HoudiniBuildError(message)
        self._houdini_result = payload

        if self._houdini_result.get("status") != "success":  # type: ignore[union-attr]
            message = (
                f"Build failed: {self._summarize_houdini_errors(self._houdini_result)}"
            )
            raise HoudiniBuildError(message)

    @staticmethod
    def _parse_houdini_result_payload(stdout: str) -> dict[str, Any] | None:
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
        messages: list[str] = []
        for entry in payload.get("errors", []):
            if isinstance(entry, dict) and entry.get("message"):
                messages.append(str(entry["message"]))

        publish_payload = payload.get("publish")
        if isinstance(publish_payload, dict):
            for entry in publish_payload.get("errors", []):
                if isinstance(entry, dict) and entry.get("message"):
                    messages.append(str(entry["message"]))

        if messages:
            return "; ".join(messages)
        return "Unknown error"


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
