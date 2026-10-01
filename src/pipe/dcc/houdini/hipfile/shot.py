from __future__ import annotations

import logging
import traceback
from enum import Enum
from pathlib import Path
from typing import cast

import hou

from pipe.dcc.houdini import runtime as houdini_runtime
from pipe.core.ui import FilteredListDialog, MessageDialog
from pipe.dcc.houdini.hipfile.departments import (
    DEPARTMENT_OPTIONS,
    PUBLISHING_DEPARTMENTS,
    Department,
)
from pipe.core.sets import current_layer_path
from pipe.core.shotgrid import (
    Set,
    SGEntity,
    Shot,
    ShotGridNotFound,
)
from pipe.dcc.houdini.util import nodetypes

from .filemanager import HFileManager
from .shot_sets import SETS_NODE_NAME, create_sets_node, sync_sets

log = logging.getLogger(__name__)


class HShotFileManager(HFileManager):
    _department: str | None

    # `Department` is the canonical enum (see `dcc/houdini/hipfile/departments.py`);
    # the class alias is kept so call sites that reference `HShotFileManager.DEPARTMENT`
    # continue to work without touching every site.
    DEPARTMENT = Department

    @classmethod
    def _department_options(cls) -> list[str]:
        return list(DEPARTMENT_OPTIONS)

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
            houdini_runtime.get_main_qt_window(),
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
        self._sync_sets(shot)
        # update SHOT_SUBSTEPS variable, this is read by the
        # sync_motion_substeps HDA
        hou.putenv("SHOT_SUBSTEPS", str(shot.substeps))

    def _department_value(self) -> str:
        normalized = str(self._department or "").strip()
        return normalized or "unknown"

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        shot = cast(Shot, entity)
        try:
            super()._setup_file(path, entity)
            self._set_shot_context(shot)
            stage = self._get_stage()
            muted_departments = self._get_muted_departments()

            sets = create_sets_node(stage)
            load_layers = self._create_load_layers(
                stage=stage, muted_departments=muted_departments
            )
            load_layers.setInput(0, sets)
            layer_break = stage.createNode("layerbreak")
            layer_break.setInput(0, load_layers)

            department_name = self._department_value().upper()
            begin_dep = stage.createNode("null")
            begin_dep.setName(f"BEGIN_{department_name}")

            end_dep = stage.createNode("null")
            end_dep.setName(f"END_{department_name}")

            begin_dep.setInput(0, layer_break)
            end_dep.setInput(0, begin_dep)
            if self._department in PUBLISHING_DEPARTMENTS:
                publish = stage.createNode(nodetypes.PUBLISH, "PUBLISH")
                publish.setInput(0, end_dep)

            end_dep.setPosition((0, 1))
            begin_dep.setPosition((0, 4))
            layer_break.setPosition((0, 5))
            load_layers.setPosition((0, 6))
            sets.setPosition((0, 7))

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
        if isinstance(exc, ShotGridNotFound):
            entity_type = exc.entity_type.lower()
            if entity_type == "sequence":
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
            if houdini_runtime.is_headless():
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

            parent = houdini_runtime.get_main_qt_window()
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
        stage: hou.Node = hou.node("/stage")  # type: ignore
        return stage

    def _set_shot_context(self, shot: Shot) -> None:
        hou.setContextOption("SHOT", shot.shot_path)

    def _set_playbar_ranges(self, shot: Shot) -> None:
        cut_in, cut_out = shot.frame_range
        hou.playbar.setFrameRange(cut_in - 5, cut_out + 5)
        hou.playbar.setPlaybackRange(cut_in - 5, cut_out + 5)

    def _sync_sets(self, shot: Shot) -> None:
        sets = shot.sets or []
        node = self._get_stage().node(SETS_NODE_NAME)
        if node is None:
            if sets:
                MessageDialog(
                    self._main_window,
                    f"This hip has no {SETS_NODE_NAME} node, so these sets weren't "
                    f"loaded: {_names(sets)}. Add a Reference LOP named "
                    f"{SETS_NODE_NAME} above the load layers node, then reopen the shot.",
                    "Shot Sets",
                ).exec_()
            return

        added = sync_sets(node, sets)
        if not added:
            return
        unpublished = [
            set for set in added if not current_layer_path(set.name).exists()
        ]
        lines = [f"Added to the {SETS_NODE_NAME} node: {_names(added)}."]
        if unpublished:
            lines.append(
                f"Not published yet: {_names(unpublished)}. The shot won't cook until "
                "they are. Disable their entries to work without them."
            )
        MessageDialog(self._main_window, "\n\n".join(lines), "Shot Sets").exec_()

    def _get_muted_departments(self) -> list[str]:
        department = self._department_value()
        if department == self.DEPARTMENT.CFX.value:
            return ["cfx", "fx", "envfx", "lighting"]
        if department == self.DEPARTMENT.FX.value:
            return ["fx"]
        if department == self.DEPARTMENT.FLO.value:
            return ["cfx", "fx", "envfx", "lighting", "flo"]
        if department == self.DEPARTMENT.ENVFX.value:
            return ["envfx"]
        if department == self.DEPARTMENT.LIGHTING.value:
            return ["lighting"]
        if department == self.DEPARTMENT.RENDER.value:
            return []
        return []

    def _create_load_layers(
        self, *, stage: hou.Node, muted_departments: list[str]
    ) -> hou.Node:
        load_layers = stage.createNode(nodetypes.LOAD_LAYERS)
        load_layers.setUserData("nodeshape", "bulge_down")
        load_layers.parm("shot").set("$JOB/`@SHOT`")  # type: ignore
        for department in muted_departments:
            load_layers.parm(f"{department}_enable").set(0)  # type: ignore
        return load_layers


def _names(sets: list[Set]) -> str:
    return ", ".join(set.display_name for set in sets)
