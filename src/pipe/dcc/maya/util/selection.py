from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from maya import cmds
from maya.api.OpenMaya import MGlobal
from maya.api.OpenMayaUI import MPxContextCommand, MPxSelectionContext

from pipe.dcc.maya.command import maya_command

if TYPE_CHECKING:
    from typing import Generator, Sequence


@contextmanager
def maintain_selection() -> Generator[None, None, None]:
    selection = cmds.ls(selection=True, long=True, ufeObjects=True, absoluteName=True)
    try:
        yield
    finally:
        cmds.select(*selection, replace=True)


def selected_edges():
    edges: list[str] = (
        cmds.filterExpand(
            cmds.ls(selection=True, flatten=True),  # type: ignore
            selectionMask=32,  # edges
        )
        or []
    )
    return edges


def objects_from_components(components: Sequence[str]) -> list[str]:
    meshes: list[str] = (
        cmds.ls(
            components,  # type: ignore
            objectsOnly=True,
        )
        or []
    )
    return meshes


CTX_NAME = "islandFaceSelectCtx"
CTX_CMD_NAME = "islandFaceSelectCtxCmd"
CUT_UV = "cutUV"


def _set_or_create_cut_uv(object: str):
    existing: list[str] = cmds.polyUVSet(object, query=True, allUVSets=True) or []  # type: ignore
    if CUT_UV not in existing:
        cmds.polyUVSet(object, create=True, uvSet=CUT_UV)
    cmds.polyUVSet(object, currentUVSet=True, uvSet=CUT_UV)
    cmds.polyPlanarProjection(object, constructionHistory=False)


def _restore_uv_set(obj: str, uv_set: str) -> None:
    existing: list[str] = cmds.polyUVSet(obj, query=True, allUVSets=True) or []  # type: ignore
    if uv_set in existing:
        cmds.polyUVSet(obj, currentUVSet=True, uvSet=uv_set)


def _delete_cut_uv(obj: str) -> None:
    existing: list[str] = cmds.polyUVSet(obj, query=True, allUVSets=True) or []  # type: ignore
    if CUT_UV in existing:
        cmds.polyUVSet(obj, delete=True, uvSet=CUT_UV)


class IslandFaceSelectContext(MPxSelectionContext):
    def __init__(self):
        super().__init__()
        self.setTitleString("Island Face Select")
        self.objects: list[str] = []
        self.previous_uv_sets: dict[str, str] = {}
        self.previous_selection: list[str] = []
        self.setImage("island_select.svg", 0)

    def _cleanup_uv_sets(self) -> None:
        for obj in self.objects:
            prev = self.previous_uv_sets.get(obj)
            if prev:
                _restore_uv_set(obj, prev)
            _delete_cut_uv(obj)

    def _cancel(self) -> None:
        self._cleanup_uv_sets()
        cmds.selectType(edge=True)
        cmds.select(*self.previous_selection, replace=True)

    def toolOnSetup(self, event):
        self.previous_selection = cmds.ls(
            selection=True, long=True, ufeObjects=True, absoluteName=True
        )
        edges = selected_edges()
        if not edges:
            MGlobal.displayWarning("Island Face Select: No edges selected.")
            self.abortAction()
            return

        objects = objects_from_components(edges)
        self.objects = objects
        self.previous_uv_sets = {
            obj: cmds.polyUVSet(obj, query=True, currentUVSet=True)[0]  # type: ignore
            for obj in objects
        }

        for obj in objects:
            _set_or_create_cut_uv(obj)

        cmds.select(edges, replace=True)
        cmds.polyMapCut()

        cmds.selectMode(component=True)
        self.setAllowPreSelectHilight()
        cmds.selectType(meshUVShell=True)
        cmds.select(clear=True)
        MGlobal.displayInfo("Click UV islands, then press Enter to convert to faces.")
        super().toolOnSetup(event)

    def toolOffCleanup(self):
        self._cancel()
        super().toolOffCleanup()

    def completeAction(self):
        faces = cmds.ls(
            cmds.polyListComponentConversion(toFace=True) or [],  # type: ignore
            flatten=True,
        )
        self._cleanup_uv_sets()

        cmds.selectType(meshUVShell=False)
        cmds.selectType(facet=True)
        if faces:
            cmds.select(faces, replace=True)  # type: ignore
        else:
            cmds.select(clear=True)
        cmds.setToolTo("selectSuperContext")

    def abortAction(self):
        self._cancel()
        super().abortAction()
        cmds.setToolTo("selectSuperContext")


class IslandFaceSelectContextCommand(MPxContextCommand):
    def __init__(self):
        super().__init__()

    def makeObj(self):
        print("MAKE OBJ")
        return IslandFaceSelectContext()

    @classmethod
    def creator(cls):
        print("CREATOR")
        return cls()


@maya_command(
    name="island_face_select",
    label="Island Face Select",
    icon="island_select.svg",
    hotkey="ctrl+alt+i",
    description="Cuts temporary seams in the mesh based on the edges selected when the tool is run, "
    "then allows selecting mesh islands. Hit enter to finish selection.",
)
def activate_island_face_select_tool():
    if not cmds.contextInfo(f"{CTX_CMD_NAME}1", exists=True):
        context = cmds.islandFaceSelectCtxCmd()  # type: ignore
        cmds.setToolTo(context)
