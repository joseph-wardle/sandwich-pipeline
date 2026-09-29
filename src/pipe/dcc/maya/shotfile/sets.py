"""A Maya scene's `/sets`: the shot's assigned sets, payloaded from their current layer."""

from __future__ import annotations

from pxr import Sdf, Usd, UsdGeom

from pipe.core.sets import current_layer_path
from pipe.core.shotgrid import Set, Shot
from pipe.core.ui import MessageDialog
from pipe.core.util.paths import get_production_path
from pipe.dcc.maya.runtime import get_main_qt_window

from .stage import get_stage

SETS_PRIM = Sdf.Path("/sets")

# Sets are authored in metres and Maya shot scenes are in centimetres.
_SET_SCALE = (100.0, 100.0, 100.0)


def sync_sets(stage: Usd.Stage, sets: list[Set]) -> list[Set]:
    """Payload each set the scene doesn't have yet, and return those sets.

    Never removes a set. A set is present if the root layer holds its `/sets/<name>`,
    active or not, so deactivating a set is how an artist drops it.
    """
    root_layer = stage.GetRootLayer()
    added = [set for set in sets if not root_layer.GetPrimAtPath(_prim_path(set))]
    if not added:
        return []
    with Usd.EditContext(stage, root_layer):
        if not root_layer.GetPrimAtPath(SETS_PRIM):
            UsdGeom.Xform.Define(stage, SETS_PRIM).AddScaleOp().Set(_SET_SCALE)
        for set in added:
            stage.DefinePrim(_prim_path(set)).GetPayloads().AddPayload(_layer_path(set))
    return added


def sync_shot_sets(shot: Shot) -> None:
    """Sync the open scene's sets with `shot`'s, and tell the artist what was added."""
    added = sync_sets(get_stage(), shot.sets or [])
    if not added:
        return
    unpublished = [set for set in added if not current_layer_path(set.name).exists()]
    lines = [f"Added to {SETS_PRIM}: {_names(added)}."]
    if unpublished:
        lines.append(
            f"Not published yet: {_names(unpublished)}. They stay empty until they "
            "are, then appear the next time the scene opens."
        )
    MessageDialog(get_main_qt_window(), "\n\n".join(lines), "Shot Sets").exec_()


def _prim_path(set: Set) -> Sdf.Path:
    return SETS_PRIM.AppendChild(set.name)


def _layer_path(set: Set) -> str:
    # Production-root-relative, resolved by `PXR_AR_DEFAULT_SEARCH_PATH`, so a scene
    # carries no mount-specific path.
    return current_layer_path(set.name).relative_to(get_production_path()).as_posix()


def _names(sets: list[Set]) -> str:
    return ", ".join(set.display_name for set in sets)
