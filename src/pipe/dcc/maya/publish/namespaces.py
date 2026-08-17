from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import attrs
import maya.cmds as mc
from pxr import Sdf

from pipe.core.ui import MessageDialog, MessageDialogCustomButtons

if TYPE_CHECKING:
    from Qt.QtWidgets import QWidget

log = logging.getLogger(__name__)


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


def unpublishable_reason(node: str) -> UnpublishableReason | None:
    """Why the animation export cannot publish this rig, or None if it can."""
    namespace = namespace_of(node)
    if Sdf.Path.IsValidIdentifier(namespace):
        return None
    if not namespace:
        return UnpublishableReason(
            "imported, not referenced",
            "Imported into the scene instead of referenced",
        )
    if ":" in namespace:
        container = _containing_reference(node)
        return UnpublishableReason(
            f"inside {container}" if container else "inside another reference",
            "Referenced inside {} rather than directly into the shot".format(
                f"'{container}'" if container else "another reference"
            ),
        )
    # Maya rewrites or rejects namespaces that aren't valid identifiers, so
    # nothing should land here. A rig must not reach the export just because we
    # ran out of explanations for it.
    return UnpublishableReason(
        "unusable namespace",
        f"Namespace '{namespace}' is not a name USD can use",
    )


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
            skipped.setdefault(reason.detail, []).append(namespace_of(node) or node)
    return publishable, skipped


def confirm_any_publishable(parent: QWidget | None, nodes: list[str]) -> bool:
    """Whether any rig here can be published. Explains it when none can."""
    publishable, skipped = partition_publishable(nodes)
    if publishable:
        return True
    _warn_nothing_publishable(parent, len(nodes), _describe_skipped(skipped))
    return False


def confirm_publishable(parent: QWidget | None, nodes: list[str]) -> list[str]:
    """The nodes to publish, or an empty list meaning stop."""
    publishable, skipped = partition_publishable(nodes)
    if not skipped:
        return publishable

    details = _describe_skipped(skipped)
    if not publishable:
        _warn_nothing_publishable(parent, len(nodes), details)
        return []

    log.warning(
        "Skipping %d of %d rigs:\n%s",
        len(nodes) - len(publishable),
        len(nodes),
        details,
    )

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

    log.warning("Cannot publish '%s': %s", rig_root, reason.detail)
    MessageDialog(
        parent,
        f"'{rig_root.split('|')[-1]}' cannot be published.\n\n"
        f"{reason.detail}.\n\n"
        "Reference the rig directly into the shot and publish it again. "
        "Nothing was exported.",
        "Cannot Publish Rig",
    ).exec_()
    return False


def _describe_skipped(skipped: dict[str, list[str]]) -> str:
    return "\n\n".join(
        "{}:\n{}".format(reason, "\n".join(f"    {name}" for name in names))
        for reason, names in skipped.items()
    )


def _warn_nothing_publishable(parent: QWidget | None, total: int, details: str) -> None:
    headline = (
        "The only rig in this scene cannot be published"
        if total == 1
        else f"None of the {total} rigs in this scene can be published"
    )
    log.warning("%s:\n%s", headline, details)
    MessageDialog(
        parent,
        f"{headline}:\n\n"
        f"{details}\n\n"
        "Reference character rigs directly into the shot to publish them. "
        "Nothing was exported.",
        "Cannot Publish Animation",
    ).exec_()


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
