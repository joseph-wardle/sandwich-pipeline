from __future__ import annotations

from typing import cast

import attrs
import maya.cmds as mc
from pxr import Sdf


@attrs.define(frozen=True)
class UnpublishableReason:
    """Why a rig cannot be published.

    `summary` sits beside a rig's name in a table, so keep it about as short as a
    commit subject; `detail` is the sentence a dialog or tooltip shows.
    """

    summary: str
    detail: str


def namespace_of(node: str) -> str:
    """The Maya namespace of a node name or DAG path, without a trailing colon."""
    return node.split("|")[-1].rpartition(":")[0]


def unpublishable_reason(cache_set: str) -> UnpublishableReason | None:
    """Why the animation export cannot publish this rig, or None if it can."""
    namespace = namespace_of(cache_set)
    if not namespace:
        return UnpublishableReason(
            "imported, not referenced",
            "Imported into the scene instead of referenced. Reference the rig "
            "directly into the shot to publish it.",
        )
    if ":" in namespace:
        container = _containing_reference(cache_set)
        return UnpublishableReason(
            f"inside {container}" if container else "inside another reference",
            "Referenced inside {} rather than directly into the shot. Reference "
            "the rig directly into the shot to publish it.".format(
                f"'{container}'" if container else "another reference"
            ),
        )
    if not Sdf.Path.IsValidIdentifier(namespace):
        return UnpublishableReason(
            "unusable namespace",
            f"Namespace '{namespace}' is not a name USD can use.",
        )
    if not mc.sets(cache_set, query=True):
        set_name = cache_set.rpartition(":")[2]
        return UnpublishableReason(
            "nothing to export",
            f"Its '{set_name}' set is empty, so the export would have no "
            "geometry to publish. Rigs built for previs are often not set up "
            "for animation publishing — check with rigging.",
        )
    if foreign_root := _root_outside_namespace(cache_set, namespace):
        return UnpublishableReason(
            f"parented under {foreign_root}",
            f"Parented under '{foreign_root}', which isn't part of the rig. "
            "Unparent the rig to the world to publish it.",
        )
    return None


def _root_outside_namespace(cache_set: str, namespace: str) -> str | None:
    """The top of the hierarchy holding the cache set's members, when that top is
    not in the rig's namespace."""
    # `sets` is typed as returning any of its flags' results; a membership
    # query returns a list of names.
    members = cast("list[str]", mc.sets(cache_set, query=True))
    for member in mc.ls(*members, long=True):
        root = member.split("|")[1].split(".")[0]
        if namespace_of(root) != namespace:
            return root
    return None


def _containing_reference(node: str) -> str | None:
    """The short filename of the reference that this node's reference sits in."""
    try:
        # `referenceQuery` is typed as returning `list[str] | str`; each of
        # these queries returns a single name.
        own_reference = cast("str", mc.referenceQuery(node, referenceNode=True))
        parent_reference = mc.referenceQuery(
            own_reference, referenceNode=True, parent=True
        )
    except RuntimeError:
        return None

    if not parent_reference:
        return None

    return cast(
        "str",
        mc.referenceQuery(
            cast("str", parent_reference),
            filename=True,
            shortName=True,
            withoutCopyNumber=True,
        ),
    )
