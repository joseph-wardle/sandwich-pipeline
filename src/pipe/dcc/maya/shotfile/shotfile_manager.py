from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import cast

import maya.api.OpenMaya as om
import maya.cmds as mc
from env_sg import DB_Config
from timeline_marker.ui import TimelineMarker  # type: ignore[import-not-found]

from pipe.core.ui import (
    RESTORE_CANCEL,
    RESTORE_SAVE_FIRST,
    MessageDialog,
    prompt_restore_conflict,
)
from pipe.core.ui.save_version_dialog import SaveVersionDialog
from pipe.core.ui.version_browser import VersionBrowserWidget
from pipe.dcc.maya.runtime import get_main_qt_window
from pipe.dcc.maya.util.on_open import install_on_open_node
from pipe.core.shotgrid import (
    SGEntity,
    Shot,
    ShotGrid,
    validate_shot_code_token,
)
from pipe.core.util import FileManager, log_errors
from pipe.core.versioning import (
    VersionRecord,
    VersionStreamSpec,
    list_version_records,
    resolve_working_file_version,
    restore_version,
    restored_message,
    saved_message,
)
from pipe.core.versioning import (
    save_version as _save_version,
)

from .stage import (
    build_shot_stage,
    get_stage,
    get_stage_shape,
    shot_override_layer_path,
)
from .timeline import shot_timeline_generator

log = logging.getLogger(__name__)


class MShotFileManager(FileManager):
    shot: Shot

    def __init__(self, **kwargs) -> None:
        conn = ShotGrid.connect(DB_Config)
        window = get_main_qt_window()
        super().__init__(conn, Shot, window, versioning=True, **kwargs)

    @classmethod
    def _shot_code_from_file_info(cls) -> str | None:
        info = mc.fileInfo("code", query=True)
        if isinstance(info, (list, tuple)):
            if not info:
                return None
            raw_value = info[0]
        elif isinstance(info, str):
            raw_value = info
        else:
            return None

        try:
            return validate_shot_code_token(raw_value)
        except ValueError:
            log.warning("Invalid shot code in scene metadata: %s", raw_value)
            return None

    @classmethod
    def _shot_code_from_scene_path(cls, scene_path: str | None) -> str | None:
        """Resolve shot code from a scene path using canonical shot folder semantics.

        Preferred source is the directory token immediately after `shot/`.
        Falls back to the scene filename stem for legacy/non-canonical paths.
        """
        if not scene_path:
            return None
        path = Path(scene_path)
        try:
            shot_index = path.parts.index("shot")
            if shot_index + 1 < len(path.parts):
                try:
                    return validate_shot_code_token(path.parts[shot_index + 1])
                except ValueError:
                    log.warning("Invalid shot token in scene path: %s", scene_path)
        except ValueError:
            pass
        stem = path.stem
        if not stem:
            return None
        try:
            return validate_shot_code_token(stem.split(".")[0])
        except ValueError:
            return None

    @classmethod
    @log_errors
    def run_on_open(cls) -> None:
        """Function to run on file open via script node"""

        # save edit target layer on save
        beforeSaveId = om.MSceneMessage.addCallback(
            om.MSceneMessage.kBeforeSave,
            lambda _: get_stage().GetEditTarget().GetLayer().Save(),
        )

        # remove callback before opening a new file
        om.MSceneMessage.addCallback(
            om.MSceneMessage.kBeforeOpen,
            lambda kwargs: om.MSceneMessage.removeCallback(kwargs["ID"]),
            {"ID": beforeSaveId},
        )

        # change default render resolution
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore

        # set session USD target layer to the override layer
        try:
            shot_code = cls._shot_code_from_file_info()
            if not shot_code:
                scene_path = mc.file(query=True, sceneName=True)
                scene_path_str = scene_path if isinstance(scene_path, str) else ""
                shot_code = cls._shot_code_from_scene_path(scene_path_str)
                if shot_code:
                    mc.fileInfo("code", shot_code)
                else:
                    mc.warning("Could not determine shot code; USD edit target not set")
                    return
            assert shot_code is not None
            mc.mayaUsdEditTarget(  # type: ignore
                get_stage_shape(),
                edit=True,
                editTarget=shot_override_layer_path(shot_code),
            )

            conn = ShotGrid.connect(DB_Config)
            shot = conn.get_shot(code=shot_code)

            # Import Timeline
            frames, colors, comments = shot_timeline_generator(
                shot.cut_duration or 0, shot.cut_in or 1001
            )
            TimelineMarker.clear()
            TimelineMarker.set(frames, colors, comments)
            mc.playbackOptions(
                animationStartTime=frames[0],
                animationEndTime=frames[-1],
                minTime=frames[0],
                maxTime=frames[-1],
            )
        except Exception:
            # Workflow boundary: many things can fail during file-open setup
            # (ShotGrid lookup, USD edit target, timeline marker). Log + warn
            # rather than crash the open.
            log.exception("run_on_open failed")
            mc.error(
                "Could not finish file-open setup. Check the script editor for details."
            )

    def _check_unsaved_changes(self) -> bool:
        if mc.file(query=True, modified=True):
            warning_response = mc.confirmDialog(
                title="Do you want to save?",
                message="The current file has not been saved. Continue anyways?",
                button=["Continue", "Cancel"],
                defaultButton="Cancel",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if warning_response == "Cancel":
                return False
        return True

    def _current_scene_path(self) -> Path | None:
        scene_raw = mc.file(query=True, sceneName=True)
        if not isinstance(scene_raw, str) or not scene_raw:
            return None
        return Path(scene_raw).expanduser().resolve()

    def _ensure_scene_saved(self) -> Path | None:
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                "Scene must be saved before creating a version.",
                "Save Required",
            ).exec_()
            return None

        if mc.file(query=True, modified=True):
            response = mc.confirmDialog(
                title="Save Changes",
                message="This scene has unsaved changes. Save before creating a version?",
                button=["Save", "Cancel"],
                defaultButton="Save",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if response != "Save":
                return None
            try:
                mc.file(save=True, force=True)
            except Exception:
                MessageDialog(
                    self._main_window,
                    "Failed to save the current scene. Resolve any file issues and try again.",
                    "Save Failed",
                ).exec_()
                log.exception("Failed to save Maya shot scene before creating version.")
                return None
            scene_path = self._current_scene_path()
            if scene_path is None:
                MessageDialog(
                    self._main_window,
                    "Could not resolve the current scene path after save.",
                    "Save Failed",
                ).exec_()
                return None

        return scene_path

    def _resolve_shot_for_scene(self, scene_path: Path) -> Shot | None:
        shot_code = self._shot_code_from_file_info() or self._shot_code_from_scene_path(
            str(scene_path)
        )
        if not shot_code:
            return None

        shot = self._conn.get_shot(code=shot_code)
        if shot.code:
            mc.fileInfo("code", shot.code)
        return shot

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        shot = cast(Shot, entity)
        return shot.code or "", "mb"

    def _open_file(self, path: Path) -> None:
        mc.file(str(path), open=True, force=True)

    def _post_open_file(self, entity: SGEntity) -> None:
        install_on_open_node(self)

    @abstractmethod
    def _setup_scene(self) -> None:
        """Fill the stage. Runs before the root layer is locked."""
        ...

    def _setup_file(self, path: Path, entity) -> None:
        mc.file(rename=str(path))
        self.shot = cast(Shot, entity)
        build_shot_stage(self.shot, populate=self._setup_scene)
        mc.file(save=True, force=True)

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        """Return (stream, owner_label, shot) for the current scene, or None.

        Subclasses must override this to resolve the versioning stream that
        corresponds to the open scene file.  ``owner_label`` is displayed in
        the version browser header.  ``shot`` is passed to ``_post_open_file``
        after opening a backup version.
        """
        ...

    def _entity_label(self) -> str:
        """Human-readable noun for the entity kind managed by this class.

        Used in dialog messages, e.g. ``\"animation\"``, ``\"RLO\"``.
        """
        return "shot"

    # ------------------------------------------------------------------
    # Shared version browser and save
    # ------------------------------------------------------------------

    def open_version_browser(self) -> None:
        kind = self._entity_label()
        scene_path = self._current_scene_path()
        if scene_path is None:
            MessageDialog(
                self._main_window,
                f"No valid {kind} shot file is open. Use Open {kind} first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                f"Could not resolve the current scene to a valid {kind} shot file. "
                f"Use Open {kind} first.",
                "Version History",
            ).exec_()
            return

        stream, owner_label, shot = resolved
        records = list_version_records(stream)
        if not records:
            MessageDialog(
                self._main_window,
                f"No version history was found for this {kind}.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            owner_label=owner_label,
            stream_label=stream.label,
        )
        if not browser.exec_():
            return

        selected_record = browser.get_selected_record()
        selected_action = browser.get_selected_action()
        if selected_record is None:
            return

        if selected_action == VersionBrowserWidget.ACTION_RESTORE:
            self._restore_version(selected_record, stream, shot)

    def _restore_version(
        self, record: VersionRecord, stream: VersionStreamSpec, entity: Shot
    ) -> None:
        kind = self._entity_label()
        if self._has_unversioned_work(stream):
            choice = prompt_restore_conflict(self._main_window)
            if choice == RESTORE_CANCEL:
                return
            if choice == RESTORE_SAVE_FIRST and not self._save_named_version(stream):
                return

        try:
            working_path = restore_version(record, stream)
        except Exception as exc:
            log.exception("Failed to restore %s version.", kind)
            MessageDialog(
                self._main_window,
                f"Failed to restore version:\n{exc}",
                "Restore Version Failed",
            ).exec_()
            return

        try:
            self._open_file(working_path)
            self._post_open_file(entity)
        except Exception as exc:
            log.exception("Restored %s version but could not open it.", kind)
            MessageDialog(
                self._main_window,
                f"Restored the version but could not open it:\n{exc}",
                "Restore Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            restored_message(record),
            "Version Restored",
        ).exec_()

    def _has_unversioned_work(self, stream: VersionStreamSpec) -> bool:
        if mc.file(query=True, modified=True):
            return True
        return resolve_working_file_version(stream) is None

    def _write_named_version(self, scene_path: Path, stream: VersionStreamSpec) -> bool:
        """Prompt for a version title and write a backup of *scene_path*."""
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return False

        try:
            record = _save_version(
                scene_path,
                stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save %s version.", self._entity_label())
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return False

        MessageDialog(
            self._main_window,
            saved_message(record),
            "Version Saved",
        ).exec_()
        return True

    def _save_named_version(self, stream: VersionStreamSpec) -> bool:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return False
        return self._write_named_version(scene_path, stream)

    def save_version_for_current_scene(self) -> None:
        scene_path = self._ensure_scene_saved()
        if scene_path is None:
            return

        resolved = self._resolve_current_stream(scene_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                f"Could not resolve the current scene to a valid {self._entity_label()} shot file.",
                "Shot Not Resolved",
            ).exec_()
            return

        stream, _, _ = resolved
        self._write_named_version(scene_path, stream)
