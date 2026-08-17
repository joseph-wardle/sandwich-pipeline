from __future__ import annotations

import logging
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
from pxr import Sdf

from .namespaces import namespace_of, unpublishable_reason
from .prim_paths import ANIM_CLASS_PATH, RIG_SCOPE_PATH

if TYPE_CHECKING:
    from pipe.core.struct.timeline import Timeline

log = logging.getLogger(__name__)


class AnimStream(Enum):
    """Which of the two parallel anim publishes a run writes."""

    MAIN = "main"
    SPLINE = "spline"

    @property
    def publish_filename(self) -> str:
        return f"{self.value}.usd"


class RigState(Enum):
    PUBLISHED = "published"
    NEVER_PUBLISHED = "never_published"
    RANGE_CHANGED = "range_changed"
    UNPUBLISHABLE = "unpublishable"

    @property
    def included(self) -> bool:
        """Whether this rig starts out marked for publishing."""
        return self is not RigState.UNPUBLISHABLE

    @property
    def locked(self) -> bool:
        """Whether the artist may change their mind about it."""
        return self is not RigState.PUBLISHED


@attrs.define(frozen=True)
class PublishedAnim:
    """One rig's entry in a shot's existing anim publish."""

    namespace: str
    anim_layer: Path
    # Publishes made before the index gained its rig scope hold animation but
    # record no rig asset.
    rig_asset: Path | None
    frames: tuple[int, int] | None
    modified: float

    def covers(self, timeline: Timeline) -> bool:
        return self.frames == (timeline.preroll, timeline.end)


@attrs.define(frozen=True)
class RigRow:
    node: str
    label: str
    state: RigState
    status: str
    detail: str
    published: PublishedAnim | None


def survey_rigs(
    nodes: list[str], publish_path: Path, timeline: Timeline
) -> list[RigRow]:
    """One row per rig in the scene, in scene order."""
    published = read_published_anim(publish_path)
    return [_row_for(node, published, timeline) for node in nodes]


def read_published_anim(publish_path: Path) -> dict[str, PublishedAnim]:
    """What the shot's current anim publish holds, keyed by rig namespace."""
    if not publish_path.is_file():
        return {}

    layer = _open_layer(publish_path)
    if layer is None:
        return {}

    anim_scope = layer.GetPrimAtPath(ANIM_CLASS_PATH)
    if anim_scope is None:
        log.warning(
            "'%s' has no %s — treating it as empty", publish_path, ANIM_CLASS_PATH
        )
        return {}

    folder = publish_path.parent
    entries: dict[str, PublishedAnim] = {}
    for spec in anim_scope.nameChildren:
        anim_layer = _referenced_asset(spec, folder)
        if anim_layer is None:
            log.warning(
                "'%s' indexes rig '%s' without referencing any animation; ignoring it",
                publish_path,
                spec.name,
            )
            continue
        try:
            modified = anim_layer.stat().st_mtime
        except OSError:
            # The shot has already lost this rig's animation, so republishing is
            # the fix. Dropping it here is what makes the row say so.
            log.warning(
                "'%s' indexes rig '%s' as '%s', which is not on disk; ignoring it",
                publish_path,
                spec.name,
                anim_layer,
            )
            continue
        entries[spec.name] = PublishedAnim(
            namespace=spec.name,
            anim_layer=anim_layer,
            rig_asset=_referenced_asset(
                layer.GetPrimAtPath(RIG_SCOPE_PATH.AppendChild(spec.name)), folder
            ),
            frames=_published_frames(anim_layer),
            modified=modified,
        )
    return entries


def _row_for(
    node: str, published: dict[str, PublishedAnim], timeline: Timeline
) -> RigRow:
    namespace = namespace_of(node)
    reason = unpublishable_reason(node)
    entry = published.get(namespace)

    if reason is not None:
        detail = f"{reason.detail}, so its animation cannot be exported."
        if entry is not None:
            detail += " The animation already in the shot is left untouched."
        else:
            detail += " Reference the rig directly into the shot to publish it."
        state, status = RigState.UNPUBLISHABLE, reason.summary
    elif entry is None:
        state = RigState.NEVER_PUBLISHED
        status = "never published"
        detail = "This rig has no animation in the shot yet, so it is always included."
    elif not entry.covers(timeline):
        state = RigState.RANGE_CHANGED
        status = "frame range changed"
        detail = (
            f"Published over frames {_range_text(entry)}, but this shot now runs "
            f"{timeline.preroll}–{timeline.end}. It is always included so the "
            "shot's rigs stay on one timeline."
        )
    else:
        state = RigState.PUBLISHED
        status = f"published {_age_text(entry.modified)}"
        detail = (
            f"Already published over frames {_range_text(entry)}. Leave it "
            "unchecked to keep that animation exactly as it is."
        )

    return RigRow(
        node=node,
        label=namespace or node,
        state=state,
        status=status,
        detail=detail,
        published=entry,
    )


def _open_layer(path: Path, *, metadata_only: bool = False) -> Sdf.Layer | None:
    """The layer as it is on disk."""
    try:
        layer = Sdf.Layer.OpenAsAnonymous(str(path), metadataOnly=metadata_only)
    except Exception:
        log.warning("Could not open '%s'", path, exc_info=True)
        return None
    return layer or None


def _referenced_asset(spec: Sdf.PrimSpec | None, folder: Path) -> Path | None:
    """The first asset a prim references, resolved against the publish's folder."""
    if spec is None:
        return None
    for reference in spec.referenceList.GetAddedOrExplicitItems():
        if reference.assetPath:
            return folder / reference.assetPath
    return None


def _published_frames(anim_layer: Path) -> tuple[int, int] | None:
    """The frame range `UsdUtils.StitchClips` stamped into a stitched layer."""
    layer = _open_layer(anim_layer, metadata_only=True)
    if layer is None or not (layer.HasStartTimeCode() and layer.HasEndTimeCode()):
        return None
    return int(layer.startTimeCode), int(layer.endTimeCode)


def _range_text(entry: PublishedAnim) -> str:
    if entry.frames is None:
        return "an unrecorded range"
    return "{}–{}".format(*entry.frames)


def _age_text(modified: float) -> str:
    seconds = time.time() - modified
    if seconds < 90:
        return "just now"
    for limit, per_unit, unit in (
        (3600, 60, "minute"),
        (86400, 3600, "hour"),
        (86400 * 30, 86400, "day"),
    ):
        if seconds < limit:
            count = int(seconds // per_unit)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return time.strftime("on %b %-d", time.localtime(modified))
