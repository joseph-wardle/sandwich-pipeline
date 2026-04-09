from __future__ import annotations

import logging
import os
import platform
import shutil
import time
import traceback
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import maya.cmds as mc
from env_sg import DB_Config

import pipe
from pipe.db import DB
from pipe.glui.dialogs import FilteredListDialog, MessageDialog
from pipe.m.util import maintain_selection
from pipe.struct.db import SGEntity

if TYPE_CHECKING:
    from Qt.QtWidgets import QWidget

log = logging.getLogger(__name__)


class Publisher:
    """Class for publishing USDs out of Maya"""

    _conn: DB
    _dialog: FilteredListDialog
    _dialog_T: type[FilteredListDialog]
    _entity: SGEntity
    _publish_path: Path
    _selected_item: str
    _system: str
    _use_sg_entity: bool
    _window: QWidget | None

    def __init__(
        self, dialog: type[FilteredListDialog] | None = None, use_sg_entity: bool = True
    ) -> None:
        self._conn = DB.Get(DB_Config)
        self._window = pipe.m.local.get_main_qt_window()
        self._system = platform.system()
        self._dialog_T = dialog or FilteredListDialog
        self._use_sg_entity = use_sg_entity

    @staticmethod
    def _assert_not_none(fun):
        @wraps(fun)
        def wrap(*args, **kwargs):
            result = fun(*args, **kwargs)
            if result is None:
                raise AssertionError
            return result

        return wrap

    def __init_subclass__(cls, *args, **kwargs) -> None:
        """Wrap overridden definitions of these methods"""
        super().__init_subclass__(*args, **kwargs)
        funcs = (cls._get_entity_from_name, cls._get_save_path)
        for f in funcs:
            setattr(cls, f.__name__, cls._assert_not_none(f))

    @property
    def _IS_WINDOWS(self) -> bool:
        return self._system == "Windows"

    def _prepublish(self) -> bool:
        """Runs before any other part of the publish function"""
        return True

    def _get_entity_list(self) -> list[str]:
        """Get a list of strings to prompt in the dialog"""
        return []

    @_assert_not_none
    def _get_entity_from_name(self, display_name: str) -> SGEntity | None:
        """Turn the chosen display name into a SG entity"""
        return None

    @_assert_not_none
    def _get_save_path(self) -> Path | None:
        """Get the save path"""
        if user_select := mc.fileDialog2(fileFilter="*.usd"):
            return Path(user_select[0])
        return None

    def _presave(self) -> bool:
        """Run before any files are saved out"""
        return True

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        """A dictionary of additional arguments to `mc.mayaUSDExport`"""
        return {}

    def _get_confirm_message(self) -> str:
        return f"The selected objects have been exported to {self._publish_path}"

    def _get_publish_telemetry_binding(self) -> tuple[str, str] | None:
        try:
            from pipe.telemetry import events
        except Exception:
            return None

        # Restrict publish telemetry to contract-declared events.
        publish_bindings = {
            "pipe.m.publish.asset": (events.EVENT_PUBLISH_ASSET_USD, "asset"),
            "pipe.m.publish.anim": (events.EVENT_PUBLISH_ANIM_USD, "anim"),
            "pipe.m.publish.camera": (events.EVENT_PUBLISH_CAMERA_USD, "camera"),
            "pipe.m.publish.customanim": (
                events.EVENT_PUBLISH_CUSTOMANIM_USD,
                "customanim",
            ),
            "pipe.m.publish.previs_asset": (
                events.EVENT_PUBLISH_PREVIS_ASSET_USD,
                "previs_asset",
            ),
        }
        return publish_bindings.get(self.__class__.__module__)

    def _new_publish_telemetry_state(self) -> dict[str, object] | None:
        binding = self._get_publish_telemetry_binding()
        if binding is None:
            return None
        try:
            from pipe.telemetry import new_action_id
        except Exception:
            return None

        event_type, publish_type = binding
        return {
            "action_id": new_action_id(),
            "event_type": event_type,
            "publish_type": publish_type,
            "started_at": time.perf_counter(),
            "emitted": False,
        }

    def _get_publish_scope(self) -> dict[str, str] | None:
        try:
            from pipe.telemetry import extract_scope
        except Exception:
            return None

        scope_sources: list[object] = []
        for attr_name in ("_entity", "_shot", "_scene_asset"):
            value = getattr(self, attr_name, None)
            if value is not None:
                scope_sources.append(value)

        if not scope_sources:
            return None

        scope = extract_scope(*scope_sources)
        return scope or None

    @staticmethod
    def _get_publish_error_code(code_name: str) -> str | None:
        try:
            from pipe.telemetry.registry import (
                ERROR_PUBLISH_COPY_FAILED,
                ERROR_PUBLISH_PRECHECK_FAILED,
                ERROR_USD_EXPORT_FAILED,
                ERROR_WINDOWS_MOVE_FAILED,
            )
        except Exception:
            return None

        error_codes = {
            "precheck": ERROR_PUBLISH_PRECHECK_FAILED,
            "export": ERROR_USD_EXPORT_FAILED,
            "copy": ERROR_PUBLISH_COPY_FAILED,
            "windows_move": ERROR_WINDOWS_MOVE_FAILED,
        }
        return error_codes.get(code_name)

    def _emit_publish_terminal_event(
        self,
        telemetry_state: dict[str, object] | None,
        *,
        status: str,
        publish_path: Path | str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        if telemetry_state is None:
            return
        if telemetry_state.get("emitted"):
            return
        telemetry_state["emitted"] = True

        try:
            from pipe.telemetry import STATUS_ERROR, STATUS_SUCCESS, emit
        except Exception:
            return

        if status == "success":
            status_value = STATUS_SUCCESS
        else:
            status_value = STATUS_ERROR

        publish_path_value = publish_path
        if publish_path_value is None:
            publish_path_value = getattr(self, "_publish_path", "")
        payload = {
            "publish_type": str(telemetry_state["publish_type"]),
            "publish_path": str(publish_path_value or ""),
        }
        started_at_raw = telemetry_state.get("started_at")
        started_at = (
            float(started_at_raw)
            if isinstance(started_at_raw, (int, float))
            else time.perf_counter()
        )
        duration_ms = max(
            0,
            int((time.perf_counter() - started_at) * 1000),
        )

        error_data = None
        if status == "error":
            if not error_code:
                return
            error_data = {
                "code": error_code,
                "message": error_message or "Publish failed",
                "exception_type": exception_type or "RuntimeError",
            }

        emit(
            str(telemetry_state["event_type"]),
            status=status_value,
            action_id=str(telemetry_state["action_id"]),
            payload=payload,
            metrics={"duration_ms": duration_ms},
            scope=self._get_publish_scope(),
            error=error_data,
        )

    def _emit_publish_error(
        self,
        telemetry_state: dict[str, object] | None,
        *,
        error_code_name: str,
        error_message: str,
        exception_type: str,
        publish_path: Path | str | None = None,
    ) -> None:
        self._emit_publish_terminal_event(
            telemetry_state,
            status="error",
            publish_path=publish_path,
            error_code=self._get_publish_error_code(error_code_name),
            error_message=error_message,
            exception_type=exception_type,
        )

    def _emit_publish_success(
        self,
        telemetry_state: dict[str, object] | None,
        *,
        publish_path: Path | str | None = None,
    ) -> None:
        self._emit_publish_terminal_event(
            telemetry_state,
            status="success",
            publish_path=publish_path,
        )

    @staticmethod
    def _restart_publish_telemetry_timer(
        telemetry_state: dict[str, object] | None,
    ) -> None:
        if telemetry_state is None:
            return
        if telemetry_state.get("emitted"):
            return
        telemetry_state["started_at"] = time.perf_counter()

    def publish(self):
        """Generic publishing function.
        `Exporter().publish()` will publish the selected geometry to the place
        chosen in the pop-up dialog, accounting for the USD export bug on
        Windows. Specific functionality is defined by passing a
        `FilteredListDialog` class into `__init__` and by overriding the
        following functions:
          - `prepublish(self)`
          - `get_entity_list(self) -> list[str]`
          - `get_entity_from_name(self, display_name: str) -> SGEntity`
          - `get_save_path(self) -> Path`
          - `presave(self)`
          - `get_mayausd_kwargs(self) -> dict[str, Any]`
        """
        publish_telemetry = self._new_publish_telemetry_state()
        publish_path: Path | None = None
        try:
            with maintain_selection():
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
                    from pipe.m.publish.asset import (
                        PublishAssetOptionsDialog,
                        PublishAssetPickerDialog,
                    )
                    from pipe.m.publish.previs_asset import PublishPrevisAssetDialog

                    dialog_type = cast(Any, self._dialog_T)
                    if self._dialog_T in (
                        PublishAssetOptionsDialog,
                        PublishAssetPickerDialog,
                        PublishPrevisAssetDialog,
                    ):
                        # Pass extra parameter (conn) if dialog needs DB access
                        self._dialog = dialog_type(
                            self._window, entity_list, self._conn
                        )
                    else:
                        # Otherwise, use the dialog normally
                        self._dialog = self._dialog_T(self._window, entity_list)

                    if not self._dialog.exec_():
                        return

                    selected_item = self._dialog.get_selected_item()
                    if selected_item is None:
                        error = MessageDialog(
                            self._window,
                            "Error: Nothing selected. Nothing exported",
                            "Error",
                        )
                        error.exec_()
                        self._emit_publish_error(
                            publish_telemetry,
                            error_code_name="precheck",
                            error_message="No publish target selected",
                            exception_type="SelectionError",
                            publish_path=publish_path,
                        )
                        return
                    self._selected_item = selected_item

                    # get the corresponding SGEntity object
                    if self._use_sg_entity:
                        try:
                            self._entity = self._get_entity_from_name(
                                self._selected_item
                            )
                        except AssertionError as exc:
                            entity_label = SGEntity.__name__
                            error = MessageDialog(
                                self._window,
                                "Error: The selected item did not correspond to a valid "
                                f"{entity_label} in ShotGrid. Please "
                                "report this error. Nothing exported",
                                "Error",
                            )
                            error.exec_()
                            self._emit_publish_error(
                                publish_telemetry,
                                error_code_name="precheck",
                                error_message=str(exc)
                                or "Selected item is not a valid SG entity",
                                exception_type=type(exc).__name__,
                                publish_path=publish_path,
                            )
                            return
                        log.debug(self._entity)

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
                temp_publish_path = str(
                    Path(os.getenv("TEMP", "")) / self._publish_path.name
                )

                kwargs = {
                    "file": str(
                        temp_publish_path if self._IS_WINDOWS else self._publish_path
                    ),
                    "selection": True,
                    "stripNamespaces": True,
                    # "writeDefaults": True,
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

                # if on Windows, work around this bug: https://github.com/PixarAnimationStudios/OpenUSD/issues/849
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

                self._emit_publish_success(
                    publish_telemetry,
                    publish_path=publish_path,
                )

                confirm = MessageDialog(
                    self._window,
                    self._get_confirm_message(),
                    "Export Complete",
                )
                confirm.exec_()
        except Exception as exc:
            self._emit_publish_error(
                publish_telemetry,
                error_code_name="precheck",
                error_message=str(exc),
                exception_type=type(exc).__name__,
                publish_path=publish_path,
            )
            raise

    def _postpublish(self) -> None:
        pass
