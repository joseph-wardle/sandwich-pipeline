from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou

from pipe.core.sets import (
    SETS_DIRNAME,
    houdini_set_stream,
    set_dir,
    valid_set_name,
)
from pipe.core.shotgrid import (
    Set,
    SGEntity,
    ShotGridError,
    ShotGridNotFound,
    normalize_display_name,
)
from pipe.core.ui import MessageDialog
from pipe.core.util.paths import get_production_path
from pipe.core.versioning import VersionStreamSpec, path_matches_stream
from pipe.dcc.houdini.util import nodetypes

from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HSetFileManager(HFileManager):
    _can_create = True

    def __init__(self) -> None:
        super().__init__(Set)

    def _entity_label(self) -> str:
        return "set"

    def _generate_filename_ext(self, entity: SGEntity) -> tuple[str, str]:
        return cast(Set, entity).name, "hipnc"

    def _prompt_create_if_not_exist(self, path: Path) -> bool:
        # Every set in ShotGrid gets its folder; there is nothing to confirm.
        path.mkdir(parents=True, exist_ok=True)
        return True

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)
        build_set_network(cast(Set, entity).name)
        hou.hipFile.save(str(path))

    def _new_entity(self) -> Set | None:
        choice, display_name = hou.ui.readInput(
            "Name the new set as people say it (e.g. Shop Interior).",
            buttons=("Next", "Cancel"),
            close_choice=1,
            title="New Set",
        )
        display_name = display_name.strip()
        if choice != 0 or not display_name:
            return None
        name = normalize_display_name(display_name)
        if not valid_set_name(name):
            self._message(
                f"{display_name!r} can't be a set name. Use letters, digits and "
                "single spaces, starting with a letter.",
                "New Set",
            )
            return None
        if set_dir(name).exists():
            self._message(
                f"The folder {SETS_DIRNAME}/{name} already exists. "
                "Choose another name, or ask a TD.",
                "New Set",
            )
            return None
        confirm = hou.ui.displayMessage(
            f"Create the set {display_name} in {SETS_DIRNAME}/{name}? "
            "Its name can't change once shots use it.",
            buttons=("Create", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="New Set",
        )
        if confirm != 0:
            return None
        try:
            return self._conn.create_set(display_name)
        except ValueError as exc:
            self._message(f"{exc} Choose another name.", "New Set")
        except ShotGridError:
            log.exception("Could not create set %s in ShotGrid.", display_name)
            self._message(
                "Could not create the set in ShotGrid. Try again, or ask a TD.",
                "New Set",
            )
        return None

    def _set_for_hip(self, hip_path: Path) -> Set | None:
        """The set whose folder holds `hip_path`, or None if it's not in a set folder.

        Raises:
            ShotGridError: ShotGrid could not be reached.
        """
        sets_root = (get_production_path() / SETS_DIRNAME).resolve()
        try:
            relative = hip_path.resolve().relative_to(sets_root)
        except ValueError:
            return None
        try:
            return self._conn.get_set(name=relative.parts[0])
        except ShotGridNotFound:
            return None

    def _resolve_current_stream(
        self, hip_path: Path
    ) -> tuple[VersionStreamSpec, str, SGEntity] | None:
        set = self._set_for_hip(hip_path)
        if set is None:
            return None
        stream = houdini_set_stream(set)
        if not path_matches_stream(hip_path, stream):
            return None
        return stream, set.name, set

    def save_version(self) -> None:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return

        resolved = self._resolve_current_stream(hip_path)
        if resolved is None:
            self._message(
                "Could not resolve the current HIP to a valid set file.",
                "Set Not Resolved",
            )
            return

        stream, _, _ = resolved
        self._write_named_version(hip_path, stream)

    def _message(self, text: str, title: str) -> None:
        MessageDialog(self._main_window, text, title).exec_()


def build_set_network(name: str) -> None:
    """Build a new set hip's /stage: `/<name>`, a Stage Manager, then the publish node."""
    stage = cast(hou.Node, hou.node("/stage"))
    root = stage.createNode("primitive")
    root.setParms(
        {
            "primpath": f"/{name}",
            "primtype": "UsdGeomXform",
            "primkind": "assembly",
            "parentprimtype": "None",
        }
    )
    manager = cast(hou.LopNode, stage.createNode("stagemanager"))
    manager.setInput(0, root)
    publish = stage.createNode(nodetypes.PUBLISH)
    publish.setInput(0, manager)
    manager.setDisplayFlag(True)
    stage.layoutChildren()
