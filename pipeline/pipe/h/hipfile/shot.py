from __future__ import annotations

import logging
import traceback
from enum import Enum
from pathlib import Path
from typing import cast

import hou

import pipe.h
from pipe.glui.dialogs import FilteredListDialog
from pipe.struct.db import SGEntity, Shot

from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HShotFileManager(HFileManager):
    _department: DEPARTMENT

    class DEPARTMENT(str, Enum):
        CFX = "cfx"
        FX = "fx"
        LIGHTING = "lighting"
        ENVFX = "envfx"
        FLO = "flo"
        RENDER = "render"

    def __init__(self):
        department_dialog = FilteredListDialog(
            pipe.h.local.get_main_qt_window(),
            [
                self.DEPARTMENT.CFX,
                self.DEPARTMENT.FX,
                self.DEPARTMENT.ENVFX,
                self.DEPARTMENT.FLO,
                self.DEPARTMENT.LIGHTING,
                self.DEPARTMENT.RENDER,
            ],
            "Department Select",
            include_filter_field=False,
            accept_button_name="Select",
        )
        department_dialog.exec_()

        self._department = department_dialog.get_selected_item()
        if not self._department:
            return
        super().__init__(Shot, versioning=True, version_glob="{}_v*.{}")

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        return self._department, "hipnc"

    def _get_subpath(self) -> str:
        return self._department

    def _post_open_file(self, entity: SGEntity) -> None:
        shot = cast(Shot, entity)
        self._set_playbar_ranges(shot)
        self._set_environment_paths(shot)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        super(HShotFileManager, HShotFileManager)._setup_file(self, path, entity)
        shot = cast(Shot, entity)
        try:
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

            begin_dep = stage.createNode("null")
            begin_dep.setName(f"BEGIN_{self._department.upper()}")

            end_dep = stage.createNode("null")
            end_dep.setName(f"END_{self._department.upper()}")

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

    def _set_environment_paths(self, shot: Shot) -> None:
        sets = shot.sets
        if sets:
            for idx, environment_stub in enumerate(sets):
                layout = self._conn.get_env_by_stub(environment_stub)
                if layout and layout.path:
                    hou.putenv(f"SET{idx+1}_PATH", layout.path)
            return

        # Fallback to depreciated single set logic if no sets are assigned
        if environment_stub := (
            shot.set or self._conn.get_sequence_by_stub(shot.sequence).set  # type: ignore[assignment, arg-type]
        ):
            layout = self._conn.get_env_by_stub(environment_stub)
            if layout and layout.path:
                hou.putenv("SET_PATH", layout.path)

    def _get_muted_departments(self) -> list[str]:
        if self._department == self.DEPARTMENT.CFX:
            return ["cfx", "fx", "envfx", "layout", "lighting", "render"]
        if self._department == self.DEPARTMENT.FX:
            return ["fx"]
        if self._department == self.DEPARTMENT.FLO:
            return ["cfx", "fx", "envfx", "lighting", "flo", "render"]
        if self._department == self.DEPARTMENT.ENVFX:
            return ["envfx"]
        if self._department == self.DEPARTMENT.LIGHTING:
            return ["lighting"]
        if self._department == self.DEPARTMENT.RENDER:
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
