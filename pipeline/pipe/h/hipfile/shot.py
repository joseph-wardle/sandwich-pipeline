from __future__ import annotations

import logging
import traceback
from enum import Enum
from pathlib import Path
from typing import cast

import hou

import pipe.h
from pipe.glui.dialogs import FilteredListDialog, MessageDialog
from pipe.shot.version_adapter import (
    houdini_department_stream,
    path_matches_stream,
    shot_owner_for,
)
from pipe.struct.db import EnvironmentStub, SGEntity, Shot, validate_shot_code_token
from pipe.versioning import VersionStreamSpec

from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HShotFileManager(HFileManager):
    _department: str | None

    def _entity_label(self) -> str:
        return "shot"

    class DEPARTMENT(str, Enum):
        CFX = "cfx"
        FX = "fx"
        LIGHTING = "lighting"
        ENVFX = "envfx"
        FLO = "flo"
        RENDER = "render"

    @classmethod
    def _department_options(cls) -> list[str]:
        return [
            cls.DEPARTMENT.CFX.value,
            cls.DEPARTMENT.FX.value,
            cls.DEPARTMENT.ENVFX.value,
            cls.DEPARTMENT.FLO.value,
            cls.DEPARTMENT.LIGHTING.value,
            cls.DEPARTMENT.RENDER.value,
        ]

    @classmethod
    def _normalize_department(cls, department: object | None) -> str | None:
        if isinstance(department, Enum):
            raw_value = department.value
        else:
            raw_value = department
        normalized = str(raw_value).strip().lower() if raw_value is not None else ""
        if normalized in cls._department_options():
            return normalized
        return None

    @classmethod
    def _prompt_department(cls) -> str | None:
        department_dialog = FilteredListDialog(
            pipe.h.local.get_main_qt_window(),
            cls._department_options(),
            "Department Select",
            include_filter_field=False,
            accept_button_name="Select",
        )
        department_dialog.exec_()
        return cls._normalize_department(department_dialog.get_selected_item())

    def __init__(
        self,
        department: DEPARTMENT | str | None = None,
        *,
        prompt_for_department: bool = True,
    ):
        self._department = self._normalize_department(department)
        if self._department is None and prompt_for_department:
            self._department = self._prompt_department()
        super().__init__(Shot, versioning=True, version_glob="{}_v*.{}")

    def open_file(self) -> None:
        if self._department is None:
            self._department = self._prompt_department()
        if self._department is None:
            return
        super().open_file()

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        department = self._department_value()
        if department == "unknown":
            raise RuntimeError("Shot department has not been selected.")
        return department, "hipnc"

    def _get_subpath(self) -> str:
        department = self._department_value()
        if department == "unknown":
            raise RuntimeError("Shot department has not been selected.")
        return department

    def _post_open_file(self, entity: SGEntity) -> None:
        shot = cast(Shot, entity)
        self._set_playbar_ranges(shot)
        self._set_environment_paths(shot)

    def _department_value(self) -> str:
        normalized = str(self._department or "").strip()
        return normalized or "unknown"

    @classmethod
    def _department_from_path(cls, hip_path: Path) -> str | None:
        parent_name = hip_path.parent.name.strip().lower()
        if parent_name in cls._department_options():
            return parent_name

        if hip_path.suffix.lower() != ".hipnc":
            return None

        stem = hip_path.stem.strip().lower()
        if ".v" in stem:
            stem = stem.rsplit(".v", 1)[0]
        if stem in cls._department_options():
            return stem
        return None

    def _resolve_shot_for_hip(self, hip_path: Path) -> Shot | None:
        try:
            shot_context = str(hou.contextOption("SHOT")).strip()
        except Exception:
            shot_context = ""

        shot_code = ""
        if shot_context:
            try:
                shot_code = validate_shot_code_token(Path(shot_context).name)
            except ValueError:
                shot_code = ""

        if not shot_code:
            try:
                shot_index = hip_path.parts.index("shot")
                if shot_index + 1 < len(hip_path.parts):
                    shot_code = validate_shot_code_token(hip_path.parts[shot_index + 1])
            except (ValueError, IndexError):
                shot_code = ""

        if not shot_code:
            return None
        return self._conn.get_shot_by_code(shot_code)

    def _resolve_current_shot_stream(
        self,
        hip_path: Path,
    ) -> tuple[Shot, str, VersionStreamSpec] | None:
        shot = self._resolve_shot_for_hip(hip_path)
        if shot is None:
            return None

        department = self._department_from_path(hip_path)
        if department is None:
            return None

        self._department = department
        stream = houdini_department_stream(
            shot,
            department,
            owner=shot_owner_for(shot),
        )
        if not path_matches_stream(hip_path, stream):
            return None
        return shot, department, stream

    def _resolve_current_stream(
        self, hip_path: Path
    ) -> tuple[VersionStreamSpec, str, SGEntity] | None:
        resolved = self._resolve_current_shot_stream(hip_path)
        if resolved is None:
            return None
        shot, department, stream = resolved
        return stream, f"{shot.code} ({department})", shot

    def save_version(self) -> None:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return

        resolved = self._resolve_current_stream(hip_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current HIP to a valid shot department file.",
                "Shot Not Resolved",
            ).exec_()
            return

        stream, _, _ = resolved
        self._do_save_version(hip_path, stream)

    def _shot_setup_payload(self, *, shot: Shot, path: Path) -> dict[str, object]:
        return {
            "entity_type": self._telemetry_entity_type(shot),
            "entity_code": self._telemetry_entity_code(shot),
            "path": str(path),
            "department": self._department_value(),
        }

    def _shot_setup_scope(self, shot: Shot) -> dict[str, str] | None:
        scope = self._telemetry_scope(shot) or {}
        department = self._department_value()
        if department:
            scope.setdefault("department", department)
        return scope or None

    @staticmethod
    def _new_shot_setup_action_id() -> str | None:
        try:
            from pipe.telemetry import new_action_id
        except Exception:
            return None
        return new_action_id()

    def _emit_shot_setup_event(
        self,
        *,
        status: str,
        shot: Shot,
        path: Path,
        action_id: str | None,
        error_message: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        try:
            from pipe.telemetry import (
                STATUS_ERROR,
                STATUS_SUCCESS,
                emit,
                events,
                get_event_definition,
            )
        except Exception:
            log.debug("Telemetry import unavailable for shot.setup", exc_info=True)
            return

        status_value = STATUS_SUCCESS if status == "success" else STATUS_ERROR
        error = None
        if status == "error":
            error_code = "SHOT_SETUP_FAILED"
            try:
                definition = get_event_definition(events.EVENT_SHOT_SETUP)
                if definition.error_codes:
                    error_code = definition.error_codes[0]
            except Exception:
                pass
            error = {
                "code": error_code,
                "message": error_message or "Shot setup failed",
                "exception_type": exception_type or "RuntimeError",
            }

        emit(
            events.EVENT_SHOT_SETUP,
            status=status_value,
            action_id=action_id,
            payload=self._shot_setup_payload(shot=shot, path=path),
            scope=self._shot_setup_scope(shot),
            error=error,
        )

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        shot = cast(Shot, entity)
        action_id = self._new_shot_setup_action_id()
        try:
            super(HShotFileManager, HShotFileManager)._setup_file(self, path, entity)
            self._set_shot_context(shot)
            stage = self._get_stage()
            muted_departments = self._get_muted_departments()

            load_layers = self._build_load_layers(
                stage=stage,
                shot=shot,
                muted_departments=muted_departments,
            )

            input_node = self._merge_load_layers(stage=stage, load_layers=load_layers)
            layer_break = stage.createNode("layerbreak")
            layer_break.setInput(0, input_node)

            department_name = self._department_value().upper()
            begin_dep = stage.createNode("null")
            begin_dep.setName(f"BEGIN_{department_name}")

            end_dep = stage.createNode("null")
            end_dep.setName(f"END_{department_name}")

            publish = stage.createNode("usd_rop")
            publish.setName("PUBLISH")
            publish.parm("lopoutput").set("$HIP/usd/main.usd")  # type: ignore[union-attr]

            begin_dep.setInput(0, layer_break)
            end_dep.setInput(0, begin_dep)
            publish.setInput(0, end_dep)

            end_dep.setPosition((0, 1))
            begin_dep.setPosition((0, 4))
            layer_break.setPosition((0, 5))
            input_node.setPosition((0, 6))

            self._post_open_file(shot)

            hou.hipFile.save()
            self._emit_shot_setup_event(
                status="success",
                shot=shot,
                path=path,
                action_id=action_id,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            log.exception(
                "Failed to setup %s shot file for %s at %s",
                self._department,
                shot.code,
                path,
            )
            message, details, error_id = self._build_setup_error(
                shot=shot,
                path=path,
                exc=exc,
                tb=tb,
            )
            log.error("Shot setup error id: %s", error_id)
            self._show_setup_error(
                title="Shot Setup Error",
                message=message,
                details=details,
            )
            self._emit_shot_setup_event(
                status="error",
                shot=shot,
                path=path,
                action_id=action_id,
                error_message=f"{error_id}: {exc}",
                exception_type=type(exc).__name__,
            )
            raise

    def _build_setup_error(
        self,
        *,
        shot: Shot,
        path: Path,
        exc: Exception,
        tb: str,
    ) -> tuple[str, str, str]:
        error_id, summary, suggestion = self._classify_setup_exception(exc, tb)

        message_lines = [
            f"Shot setup couldn't complete for {shot.code} ({self._department}).",
            summary,
            suggestion,
            "The scene may have been saved in a blank state.",
            f"Error ID: {error_id}",
        ]
        message = "\n".join(line for line in message_lines if line)

        details = (
            f"Error ID: {error_id}\n"
            f"Shot: {shot.code}\n"
            f"Department: {self._department}\n"
            f"File: {path}\n"
            f"Exception: {type(exc).__name__}: {exc}\n\n"
            f"{tb}"
        )
        return message, details, error_id

    def _classify_setup_exception(
        self,
        exc: Exception,
        tb: str,
    ) -> tuple[str, str, str]:
        if isinstance(exc, StopIteration):
            if "get_env_by_stub" in tb:
                return (
                    "SHOT_SETUP_ENV_NOT_FOUND",
                    "The environment assigned to this shot could not be found.",
                    "Check the shot's set(s) or sequence environment assignment in ShotGrid.",
                )
            if "get_sequence_by_stub" in tb:
                return (
                    "SHOT_SETUP_SEQUENCE_NOT_FOUND",
                    "The sequence assigned to this shot could not be found in ShotGrid.",
                    "Check the shot's sequence assignment.",
                )
            return (
                "SHOT_SETUP_ENTITY_NOT_FOUND",
                "Required ShotGrid data could not be found.",
                "Check the shot's ShotGrid links or ask production to verify.",
            )

        if isinstance(exc, hou.OperationFailed):
            exc_text = str(exc)
            if (
                "Invalid node type name" in exc_text
                or "Unknown operator type" in exc_text
                or "Invalid operator type" in exc_text
            ):
                return (
                    "SHOT_SETUP_HDA_MISSING",
                    "A required Houdini asset could not be created.",
                    "Make sure the Bobo Load Layers HDA is installed and up to date.",
                )
            if "Permission denied" in exc_text or "Access is denied" in exc_text:
                return (
                    "SHOT_SETUP_PERMISSION_DENIED",
                    "The scene could not be saved due to permissions.",
                    "Check folder permissions or contact Pipeline.",
                )

        if isinstance(exc, PermissionError):
            return (
                "SHOT_SETUP_PERMISSION_DENIED",
                "The scene could not be saved due to permissions.",
                "Check folder permissions or contact Pipeline.",
            )

        if (
            isinstance(exc, AttributeError)
            and "createNode" in tb
            and "NoneType" in str(exc)
        ):
            return (
                "SHOT_SETUP_STAGE_MISSING",
                "USD stage context is missing in this scene.",
                "Start from a LOP template or ensure `/stage` exists.",
            )

        if isinstance(exc, TypeError) and "_set_playbar_ranges" in tb:
            return (
                "SHOT_SETUP_CUT_RANGE_INVALID",
                "Shot cut range is invalid or missing.",
                "Check cut in/out values on the shot in ShotGrid.",
            )

        return (
            "SHOT_SETUP_UNEXPECTED",
            "An unexpected error occurred during shot setup.",
            "Contact Pipeline and include the Error ID.",
        )

    def _show_setup_error(self, *, title: str, message: str, details: str) -> None:
        try:
            if pipe.h.local.is_headless():
                print(message)
                if details:
                    print("\nDetails:\n" + details)
                return

            try:
                hou.ui.displayMessage(
                    message,
                    severity=hou.severityType.Error,
                    title=title,
                    details=details,
                )
                return
            except TypeError:
                # Older Houdini builds may not support the details argument.
                pass

            try:
                from Qt import QtCore, QtWidgets
            except Exception:
                hou.ui.displayMessage(
                    f"{message}\n\nDetails:\n{details}",
                    severity=hou.severityType.Error,
                    title=title,
                )
                return

            parent = pipe.h.local.get_main_qt_window()
            dialog = QtWidgets.QDialog(parent)
            dialog.setWindowTitle(title)
            dialog.setWindowFlags(dialog.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

            layout = QtWidgets.QVBoxLayout(dialog)
            label = QtWidgets.QLabel(message)
            label.setWordWrap(True)
            layout.addWidget(label)

            toggle = QtWidgets.QToolButton()
            toggle.setText("Show Details")
            toggle.setCheckable(True)
            toggle.setArrowType(QtCore.Qt.RightArrow)
            layout.addWidget(toggle)

            details_edit = QtWidgets.QPlainTextEdit()
            details_edit.setReadOnly(True)
            details_edit.setPlainText(details)
            details_edit.setVisible(False)
            details_edit.setMinimumHeight(240)
            layout.addWidget(details_edit)

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
            buttons.accepted.connect(dialog.accept)
            layout.addWidget(buttons)

            def _toggle_details(checked: bool) -> None:
                details_edit.setVisible(checked)
                toggle.setText("Hide Details" if checked else "Show Details")
                toggle.setArrowType(
                    QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow
                )
                dialog.adjustSize()

            toggle.toggled.connect(_toggle_details)

            dialog.exec_()
        except Exception:
            print(message)
            if details:
                print("\nDetails:\n" + details)

    def _get_stage(self) -> hou.Node:
        stage: hou.Node = hou.node("/stage")  # type: ignore[assignment]
        return stage

    def _set_shot_context(self, shot: Shot) -> None:
        hou.setContextOption("SHOT", shot.shot_path)

    def _set_playbar_ranges(self, shot: Shot) -> None:
        start = shot.cut_in - 5
        end = shot.cut_out + 5
        hou.playbar.setFrameRange(start, end)
        hou.playbar.setPlaybackRange(start, end)

    def _resolve_environment_path(
        self,
        *,
        shot: Shot,
        environment_stub: EnvironmentStub | None,
    ) -> str | None:
        if environment_stub is None:
            return None
        try:
            layout = self._conn.get_env_by_stub(environment_stub)
        except StopIteration:
            log.warning(
                "Skipping missing environment while opening shot %s: %s",
                shot.code,
                environment_stub,
            )
            return None
        return layout.path if layout and layout.path else None

    def _resolve_sequence_environment_stub(
        self,
        shot: Shot,
    ) -> EnvironmentStub | None:
        if shot.sequence is None:
            return None
        try:
            sequence = self._conn.get_sequence_by_stub(shot.sequence)
        except StopIteration:
            log.warning(
                "Skipping missing sequence while opening shot %s: %s",
                shot.code,
                shot.sequence,
            )
            return None
        return sequence.set

    def _set_environment_paths(self, shot: Shot) -> None:
        sets = shot.sets
        if sets:
            for idx, environment_stub in enumerate(sets):
                environment_path = self._resolve_environment_path(
                    shot=shot,
                    environment_stub=environment_stub,
                )
                if environment_path:
                    hou.putenv(f"SET{idx+1}_PATH", environment_path)
            return

        # Fallback to deprecated single-set logic if no sets are assigned.
        fallback_environment_stub = shot.set or self._resolve_sequence_environment_stub(
            shot
        )
        environment_path = self._resolve_environment_path(
            shot=shot,
            environment_stub=fallback_environment_stub,
        )
        if environment_path:
            hou.putenv("SET_PATH", environment_path)

    def _get_muted_departments(self) -> list[str]:
        department = self._department_value()
        if department == self.DEPARTMENT.CFX.value:
            return ["cfx", "fx", "envfx", "layout", "lighting", "render"]
        if department == self.DEPARTMENT.FX.value:
            return ["fx"]
        if department == self.DEPARTMENT.FLO.value:
            return ["cfx", "fx", "envfx", "lighting", "flo", "render"]
        if department == self.DEPARTMENT.ENVFX.value:
            return ["envfx"]
        if department == self.DEPARTMENT.LIGHTING.value:
            return ["lighting"]
        if department == self.DEPARTMENT.RENDER.value:
            return []
        return []

    def _build_load_layers(
        self,
        *,
        stage: hou.Node,
        shot: Shot,
        muted_departments: list[str],
    ) -> list[hou.Node]:
        load_layers: list[hou.Node] = []
        sets = shot.sets

        if sets:
            for idx, environment_stub in enumerate(sets):
                load_layer = self._create_load_layer(
                    stage=stage,
                    shot=shot,
                    muted_departments=muted_departments,
                    environment_stub=environment_stub,
                )
                load_layer.setPosition((idx * 2, 6))
                load_layers.append(load_layer)
            return load_layers

        # Fallback to depreciated single set logic if no sets are assigned
        env_stub = shot.set or self._conn.get_sequence_by_stub(shot.sequence).set  # type: ignore[arg-type]
        load_layer = self._create_load_layer(
            stage=stage,
            shot=shot,
            muted_departments=muted_departments,
            environment_stub=env_stub,
        )
        load_layers.append(load_layer)
        return load_layers

    def _create_load_layer(
        self,
        *,
        stage: hou.Node,
        shot: Shot,
        muted_departments: list[str],
        environment_stub,
    ) -> hou.Node:
        load_layer = stage.createNode("dbclark::main::Bobo_Load_Layers::1.0")
        load_layer.setUserData("nodeshape", "bulge_down")
        load_layer.parm("shot").set("$JOB/`@SHOT`")  # type: ignore[union-attr]

        for department in muted_departments:
            load_layer.parm(f"{department}_enable").set(0)  # type: ignore[union-attr]

        layout = (
            self._conn.get_env_by_stub(environment_stub) if environment_stub else None
        )
        if layout and layout.path:
            load_layer.parm("layout_path").set(f"$JOB/{layout.path}/main.usd")  # type: ignore[union-attr]

        return load_layer

    def _merge_load_layers(
        self,
        *,
        stage: hou.Node,
        load_layers: list[hou.Node],
    ) -> hou.Node:
        if len(load_layers) > 1:
            merge_node = stage.createNode("merge")
            merge_node.setName("LOAD_LAYERS")
            for idx, load_layer in enumerate(load_layers):
                merge_node.setInput(idx, load_layer)
            return merge_node
        if load_layers:
            return load_layers[0]

        input_node = stage.createNode("null")
        input_node.setName("NO_ENVIRONMENT", unique_name=True)
        return input_node
