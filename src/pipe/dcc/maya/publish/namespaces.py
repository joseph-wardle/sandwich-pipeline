from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import maya.cmds as mc
from pxr import Sdf

from pipe.core.ui import MessageDialog, MessageDialogCustomButtons

if TYPE_CHECKING:
    from Qt.QtWidgets import QWidget

log = logging.getLogger(__name__)


def namespace_of(node: str) -> str:
    """The Maya namespace of a node name or DAG path, without a trailing colon."""
    return node.split("|")[-1].rpartition(":")[0]


def unpublishable_reason(node: str) -> str | None:
    """Why the animation export cannot publish this rig, or None if it can."""
    namespace = namespace_of(node)
    if Sdf.Path.IsValidIdentifier(namespace):
        return None
    if not namespace:
        return "Imported into the scene instead of referenced"
    if ":" in namespace:
        container = _containing_reference(node)
        inside = f"'{container}'" if container else "another reference"
        return f"Referenced inside {inside} rather than directly into the shot"
    # Maya rewrites or rejects namespaces that aren't valid identifiers, so
    # nothing should land here. A rig must not reach the export just because we
    # ran out of explanations for it.
    return f"Namespace '{namespace}' is not a name USD can use"


def partition_publishable(nodes: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """The nodes the export can publish, and the rest grouped by why it cannot.

    Both halves stay in scene order, and a reason a dozen rigs share is listed
    once rather than a dozen times.
    """
    publishable: list[str] = []
    skipped: dict[str, list[str]] = {}
    for node in nodes:
        reason = unpublishable_reason(node)
        if reason is None:
            publishable.append(node)
        else:
            skipped.setdefault(reason, []).append(namespace_of(node) or node)
    return publishable, skipped


def confirm_publishable(parent: QWidget | None, nodes: list[str]) -> list[str]:
    """The nodes to publish, or an empty list meaning stop."""
    publishable, skipped = partition_publishable(nodes)
    if not skipped:
        return publishable

    details = "\n\n".join(
        "{}:\n{}".format(reason, "\n".join(f"    {name}" for name in names))
        for reason, names in skipped.items()
    )
    log.warning(
        "Skipping %d of %d rigs:\n%s",
        len(nodes) - len(publishable),
        len(nodes),
        details,
    )

    if not publishable:
        MessageDialog(
            parent,
            f"None of the {len(nodes)} rigs in this scene can be published:\n\n"
            f"{details}\n\n"
            "Reference character rigs directly into the shot to publish them. "
            "Nothing was exported.",
            "Cannot Publish Animation",
        ).exec_()
        return []

    keep_going = MessageDialogCustomButtons(
        parent,
        f"{len(nodes) - len(publishable)} of {len(nodes)} rigs cannot be "
        f"published and will be left out:\n\n{details}\n\n"
        f"The other {len(publishable)} will be published as normal.",
        "Some Rigs Cannot Be Published",
        has_cancel_button=True,
        ok_name="Skip and publish",
        cancel_name="Cancel",
    )
    if not keep_going.exec_():
        return []

    return publishable


def confirm_rig_publishable(parent: QWidget | None, rig_root: str) -> bool:
    """Whether this one rig can be published. Opens a dialog when it cannot."""
    reason = unpublishable_reason(rig_root)
    if reason is None:
        return True

    log.warning("Cannot publish '%s': %s", rig_root, reason)
    MessageDialog(
        parent,
        f"'{rig_root.split('|')[-1]}' cannot be published.\n\n"
        f"{reason}.\n\n"
        "Reference the rig directly into the shot and publish it again. "
        "Nothing was exported.",
        "Cannot Publish Rig",
    ).exec_()
    return False


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
