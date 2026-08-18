"""`MPrevisFileManager` — sequence-level Maya file for the previs sequencer."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import maya.cmds as mc

from pipe.core.previs import load_manifest
from pipe.core.shotgrid import SGEntity, Shot, is_previs_shot_code
from pipe.core.ui import MessageDialog
from pipe.core.util.filemanager import OpenFileDialog
from pipe.core.util.paths import get_previs_path
from pipe.core.versioning import VersionStreamSpec

from pipe.dcc.maya.shotfile import stage
from pipe.dcc.maya.shotfile.shotfile_manager import MShotFileManager

from . import dialogs, file_ops, playback, state
from .state import PrevisState


class MPrevisFileManager(MShotFileManager):
    def __init__(self) -> None:
        super().__init__(version_msg="Open older previs file")
        # Alternates ARE the history surface
        self._versioning = False

    def _entity_label(self) -> str:
        return "previs"

    def _check_unsaved_changes(self) -> bool:
        return True

    def _filter_entities(self, entities: list[SGEntity]) -> list[SGEntity]:
        return [e for e in entities if is_previs_shot_code(e.code)]

    def _compute_entity_path(self, entity: SGEntity) -> Path:
        shot = cast(Shot, entity)
        return get_previs_path() / (shot.code or "")

    def open_file(self) -> None:
        """Open a previs sequence: pick the sequence, then a file within it.

        The base flow opens one file per entity; a previs sequence holds many
        files, so once the sequence is chosen the artist picks an existing file
        or starts a new one.
        """
        if not self._check_unsaved_changes():
            return
        entity = self._pick_sequence()
        if entity is None:
            return
        sequence_dir = self._compute_entity_path(entity)
        if not self._prompt_create_if_not_exist(sequence_dir):
            return
        if not self._open_or_create_workspace(sequence_dir, entity):
            return
        self._post_open_file(entity)

    def _pick_sequence(self) -> Shot | None:
        """Prompt for a previs sequence (its <LETTER>_previs proxy Shot)."""
        shots = cast("list[SGEntity]", list(self._conn.find_shots()))
        names = sorted(e.code or "" for e in self._filter_entities(shots) if e.code)
        dialog = OpenFileDialog(
            self._main_window,
            names,
            self._entity_type,
            versioning=self._versioning,
            version_msg=self._version_msg,
        )
        if not dialog.exec_():
            return None
        code = dialog.get_selected_item()
        if not code:
            return None
        return self._conn.get_shot(code=code)

    def _open_or_create_workspace(self, sequence_dir: Path, entity: Shot) -> bool:
        """Pick a file in the sequence and open it, or start a new one.

        Returns False when the artist cancels, so the caller skips post-open setup.
        """
        sequence_code = entity.code or ""
        manifest = load_manifest(sequence_code)
        records = sorted(manifest.files.values(), key=lambda r: (r.label, r.version))
        choice = dialogs.pick_workspace_file(
            self._main_window, records, sequence_code=sequence_code
        )
        if choice is None:
            return False
        if choice.filename is None:
            return self._create_workspace(entity)
        self._open_file(sequence_dir / choice.filename)
        return True

    def _create_workspace(self, entity: Shot) -> bool:
        """Prompt for a label and create a fresh workspace file. False on cancel."""
        label = dialogs.prompt_new_label(self._main_window)
        if label is None:
            return False
        try:
            file_ops.new_file(self, entity, label)
        except file_ops.PrevisFileError as exc:
            MessageDialog(self._main_window, str(exc), "Cannot Create File").exec_()
            return False
        return True

    def _setup_scene(self) -> None:
        # Sets only. Per-shot env overrides remain the RLO's responsibility, so a
        # previs sequence has no shot-level override layer to edit into.
        stage.add_sets(self.shot)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        mc.file(newFile=True, force=True)
        mc.file(rename=str(path))

        self.shot = cast(Shot, entity)
        code = self.shot.code or ""

        # The sequence's maya_root.usd is shared by every file in it, so creating a
        # file is also when its sets are reconciled against ShotGrid.
        root_layer, _ = stage.create_stage_proxy(
            get_previs_path() / code / stage.ROOT_LAYER,
            file_path_ref="./" + stage.ROOT_LAYER,
        )
        self._setup_scene()
        root_layer.Save()
        root_layer.SetPermissionToSave(False)

        stage.serialize_usd_edits_into_scene()
        state.write_state(PrevisState())
        mc.fileInfo("code", code)
        mc.file(save=True, force=True)

    @classmethod
    def run_on_open(cls) -> None:
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore
        playback.install_camera_callback()

    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        # Previs deliberately has no version-browser surface; stub satisfies the abstract.
        return None
