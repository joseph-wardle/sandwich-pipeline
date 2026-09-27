from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou

from pipe.core.sets import (
    SETS_DIRNAME,
    commit_version,
    create_staging,
    discard_staged,
    houdini_set_stream,
    make_current,
    next_version,
    prepare_layer,
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

from .filemanager import HFileManager

log = logging.getLogger(__name__)

# The USD ROP Publish Set renders. A new set hip has one; an older hip needs its
# ROP renamed to this.
PUBLISH_ROP_NAME = "publish_set"


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
        path.mkdir(mode=0o770, parents=True, exist_ok=True)
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

    def publish(self) -> None:
        """Publish the set's next version and make it the one shots read."""
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return
        try:
            set = self._set_for_hip(hip_path)
        except ShotGridError:
            log.exception("Could not look up the set for %s.", hip_path)
            self._message(
                "Could not reach ShotGrid to look up this set. Nothing was published.",
                "Publish Set",
            )
            return
        if set is None:
            self._message(
                "This hip isn't a set's hip. Open the set with Open Set, then publish.",
                "Publish Set",
            )
            return
        rop = hou.node(f"/stage/{PUBLISH_ROP_NAME}")
        if rop is None:
            self._message(
                f"There is no USD ROP named {PUBLISH_ROP_NAME} in /stage. Add one "
                f"below the node you want to publish, name it {PUBLISH_ROP_NAME}, "
                "and publish again.",
                "Publish Set",
            )
            return
        choice, description = hou.ui.readInput(
            f"What changed in {set.display_name}?",
            buttons=("Publish", "Cancel"),
            close_choice=1,
            title="Publish Set",
        )
        if choice != 0:
            return

        version = next_version(set.name)
        label = f"{set.display_name} v{version:03d}"
        try:
            staged = create_staging(set.name, version)
        except FileExistsError as exc:
            self._message(
                f"Nothing was published: {exc.filename} already exists. Someone may "
                f"be publishing {set.display_name} right now. If not, a publish "
                "stopped partway: delete that folder and publish again.",
                "Publish Set",
            )
            return
        except OSError:
            log.exception("Could not create the staging folder for %s.", label)
            self._message(
                f"Couldn't create a folder for {label}, so nothing was published. "
                "Ask a TD to check the permissions on the set's publish folder.",
                "Publish Set",
            )
            return
        try:
            cast(hou.RopNode, rop).render(output_file=str(staged))
            written = staged.is_file()
        except hou.OperationFailed:
            # The root layer may exist even so, with a layer it needs left unwritten.
            log.exception("%s failed to write %s.", rop.path(), staged)
            written = False
        if not written:
            discard_staged(set.name, version)
            self._message(
                f"{rop.path()} failed to write the set, so nothing was published. "
                "Check the node's errors.",
                "Publish Set",
            )
            return

        try:
            stray = prepare_layer(staged, set.name, hip_path)
        except ValueError:
            discard_staged(set.name, version)
            self._message(
                f"Nothing was published: the stage has no /{set.name} prim. Put "
                f"everything the set needs under /{set.name} and publish again.",
                "Publish Set",
            )
            return
        if stray and not self._publish_anyway(set.name, stray):
            discard_staged(set.name, version)
            return

        try:
            version_path = commit_version(set.name, version)
        except OSError:
            log.exception("Could not rename %s into place.", staged.parent)
            self._message(
                f"{label} was written to {staged.parent} but couldn't be moved into "
                "place, so nothing was published. Ask a TD.",
                "Publish Set",
            )
            return
        try:
            make_current(set.name, version)
        except OSError:
            log.exception("Could not make %s current.", label)
            self._message(
                f"{label} is published, but shots still read the previous version. "
                f"A TD can run make_current({set.name!r}, {version}).",
                "Publish Set",
            )
            return
        try:
            self._conn.create_set_published_file(
                set, version=version, path=version_path, description=description
            )
        except ShotGridError:
            log.exception("Could not register %s in ShotGrid.", label)
            self._message(
                f"{label} is published and shots now read it, but ShotGrid wasn't "
                "told. Tell a TD.",
                "Publish Set",
            )
            return

        self._message(f"Published {label}. Shots now read it.", "Publish Set")

    def _publish_anyway(self, name: str, stray: list[str]) -> bool:
        listed = "\n".join(f"  /{prim}" for prim in stray)
        choice = hou.ui.displayMessage(
            f"These prims are outside /{name} and won't reach shots:\n{listed}\n\n"
            "Publish anyway?",
            buttons=("Publish", "Cancel"),
            severity=hou.severityType.Warning,
            default_choice=1,
            close_choice=1,
            title="Publish Set",
        )
        return choice == 0

    def _message(self, text: str, title: str) -> None:
        MessageDialog(self._main_window, text, title).exec_()


def build_set_network(name: str) -> None:
    """Build a new set hip's /stage: `/<name>`, a Stage Manager, then the publish ROP."""
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
    rop = stage.createNode("usd_rop", PUBLISH_ROP_NAME)
    rop.setInput(0, manager)
    manager.setDisplayFlag(True)
    stage.layoutChildren()
