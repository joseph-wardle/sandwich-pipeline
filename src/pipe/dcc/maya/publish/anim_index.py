from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
import cattrs
from pxr import Sdf

from .prim_paths import ANIM_CLASS_PATH, RIG_ROOT_PATH, RIG_SCOPE_PATH

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)


class AnimStream(Enum):
    """Which of the two parallel anim publishes a run writes.

    Each stream owns a whole set of layers in the publish folder, distinguished
    by name, so that publishing one never disturbs the other.
    """

    MAIN = "main"
    SPLINE = "spline"

    @property
    def publish_filename(self) -> str:
        return f"{self.value}.usd"

    @property
    def anim_layer_suffix(self) -> str:
        """Names `mr_yoon.anim.usd`, or `mr_yoon.spline.anim.usd` on Spline."""
        return "anim" if self is AnimStream.MAIN else f"{self.value}.anim"

    def stitched_layer_name(self, name: str) -> str:
        """Names `mr_yoon.usd`, or `mr_yoon.spline.usd` on Spline."""
        return name if self is AnimStream.MAIN else f"{name}.{self.value}"


def index_key(name: str) -> str:
    """A rig's identity in a shot's anim publish.

    A rig is identified by its Maya namespace, folded to lower case because the
    namespace also names files on disk. This is the only place that folding
    happens: everything that matches a scene rig to a published one asks here.
    """
    return name.lower()


@attrs.define(frozen=True)
class RigReference:
    """The published rig a shot's animation is applied to."""

    asset_path: Path
    prim_path: str


@attrs.define(frozen=True)
class PublishedAnim:
    """One rig's entry in a shot's anim publish."""

    name: str
    anim_layer: Path
    # None either because the publish predates the rig scope, or because
    # ShotGrid could not name the rig. Whoever indexes the entry says which.
    rig: RigReference | None


def read_anim_index(publish_path: Path) -> dict[str, PublishedAnim]:
    """What the shot's current anim publish holds, keyed by `index_key`."""
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
        anim_reference = _first_reference(spec)
        if anim_reference is None:
            log.warning(
                "'%s' indexes rig '%s' without referencing any animation; ignoring it",
                publish_path,
                spec.name,
            )
            continue
        anim_layer = folder / anim_reference.assetPath
        if not anim_layer.is_file():
            # The shot has already lost this rig's animation, so republishing is
            # the fix. Dropping it here is what makes the row say so.
            log.warning(
                "'%s' indexes rig '%s' as '%s', which is not on disk; ignoring it",
                publish_path,
                spec.name,
                anim_layer,
            )
            continue
        # Keyed by `index_key`, but named as the publish named it: keeping a rig
        # re-authors the entry the last publish wrote, never one derived now.
        entries[index_key(spec.name)] = PublishedAnim(
            name=spec.name,
            anim_layer=anim_layer,
            rig=_rig_reference_of(
                layer.GetPrimAtPath(RIG_SCOPE_PATH.AppendChild(spec.name)), folder
            ),
        )
    return entries


def author_rig_entry(
    root_layer: Sdf.Layer,
    name: str,
    anim_layer: Path,
    rig: RigReference | None,
) -> None:
    """Index one rig: a class prim carrying its animation, and a def that
    references the rig and inherits that class."""
    anim_path = ANIM_CLASS_PATH.AppendChild(name)
    if root_layer.GetPrimAtPath(anim_path):
        raise ValueError(
            f"'{name}' is being indexed twice in one publish. A rig is "
            "identified by its namespace, so two rigs cannot share one."
        )

    anim_spec = Sdf.CreatePrimInLayer(root_layer, anim_path)
    anim_spec.specifier = Sdf.SpecifierClass
    anim_spec.referenceList.Append(
        Sdf.Reference(_relative_to(root_layer, anim_layer), RIG_ROOT_PATH)
    )

    # The rig scope has to be defined, not just an over.
    rig_scope_spec = Sdf.CreatePrimInLayer(root_layer, RIG_SCOPE_PATH)
    rig_scope_spec.specifier = Sdf.SpecifierDef
    rig_scope_spec.typeName = "Scope"

    instance_spec = Sdf.CreatePrimInLayer(root_layer, RIG_SCOPE_PATH.AppendChild(name))
    instance_spec.specifier = Sdf.SpecifierDef
    instance_spec.inheritPathList.Prepend(anim_path)

    if rig is not None:
        instance_spec.referenceList.Append(
            Sdf.Reference(
                _relative_to(root_layer, rig.asset_path), Sdf.Path(rig.prim_path)
            )
        )


def verify_keepable(entries: Iterable[PublishedAnim]) -> None:
    """Raise unless every kept rig can still be indexed exactly as it is."""
    for entry in entries:
        if not entry.anim_layer.is_file():
            raise FileNotFoundError(
                f"'{entry.name}' was kept, but its animation is no longer at "
                f"'{entry.anim_layer}'. Publish that rig instead of keeping it."
            )


def published_frames(anim_layer: Path) -> tuple[int, int] | None:
    """The frame range `UsdUtils.StitchClips` stamped into a stitched layer."""
    layer = _open_layer(anim_layer, metadata_only=True)
    if layer is None or not (layer.HasStartTimeCode() and layer.HasEndTimeCode()):
        return None
    return int(layer.startTimeCode), int(layer.endTimeCode)


def entries_to_json(entries: Iterable[PublishedAnim]) -> str:
    return json.dumps(cattrs.unstructure(tuple(entries)))


def entries_from_json(data: str) -> tuple[PublishedAnim, ...]:
    return cattrs.structure(json.loads(data), tuple[PublishedAnim, ...])


def _relative_to(root_layer: Sdf.Layer, asset_path: Path) -> str:
    return Sdf.ComputeAssetPathRelativeToLayer(root_layer, asset_path.as_posix())


def _open_layer(path: Path, *, metadata_only: bool = False) -> Sdf.Layer | None:
    """The layer as it is on disk."""
    try:
        layer = Sdf.Layer.OpenAsAnonymous(str(path), metadataOnly=metadata_only)
    except Exception:
        log.warning("Could not open '%s'", path, exc_info=True)
        return None
    return layer or None


def _first_reference(spec: Sdf.PrimSpec | None) -> Sdf.Reference | None:
    if spec is None:
        return None
    for reference in spec.referenceList.GetAddedOrExplicitItems():
        if reference.assetPath:
            return reference
    return None


def _rig_reference_of(spec: Sdf.PrimSpec | None, folder: Path) -> RigReference | None:
    reference = _first_reference(spec)
    if reference is None:
        return None
    return RigReference(
        asset_path=folder / reference.assetPath,
        prim_path=reference.primPath.pathString,
    )
