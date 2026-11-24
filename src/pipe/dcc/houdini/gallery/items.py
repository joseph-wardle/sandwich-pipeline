"""What the gallery lists for a published component: one item per geometry variant."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pxr import Usd, UsdGeom

from pipe.core.assembly.model import piece_prim_name
from pipe.core.asset.paths import GEOMETRY_VARIANT_SET
from pipe.core.util.paths import production_relative_path

from . import THUMBNAIL_DIRNAME

# Houdini's drop handler turns this blind data key into the Asset Reference's
# variant selections (`layoutplugins/assets/sidefx_assets.py`).
VARIANTS_BLIND_DATA_KEY = "variants"
JOB_PREFIX = "$JOB/"

# Matches an item to the asset it lists whichever OS wrote its path.
ItemKey = tuple[str, str]


@dataclass(frozen=True)
class GalleryItem:
    label: str
    export_path: Path
    variant: str | None

    @property
    def file_path(self) -> str:
        """The path stored in the DB, which resolves on Linux and Windows alike."""
        return f"{JOB_PREFIX}{_production_relative(self.export_path)}"

    @property
    def blind_data(self) -> bytes:
        if self.variant is None:
            return b""
        selection = {VARIANTS_BLIND_DATA_KEY: {GEOMETRY_VARIANT_SET: self.variant}}
        return json.dumps(selection).encode()

    @property
    def key(self) -> ItemKey:
        return item_key(self.file_path, self.blind_data)

    @property
    def thumbnail_path(self) -> Path:
        stem = self.label if self.variant else self.export_path.stem
        return self.export_path.parent / THUMBNAIL_DIRNAME / f"{stem}.png"


def items_for(label: str, export_path: Path) -> list[GalleryItem]:
    """One item per variant that has geometry, or one for the whole asset."""
    variants = geometry_variants(export_path)
    if len(variants) < 2:
        return [GalleryItem(label=label, export_path=export_path, variant=None)]
    return [
        GalleryItem(
            label=piece_prim_name(label, variant),
            export_path=export_path,
            variant=variant,
        )
        for variant in variants
    ]


def geometry_variants(export_path: Path) -> list[str]:
    """The `geo` variants that show something.

    The component config adds `__EMPTY` and older publishes carry a
    `FALLBACK_NAME`; both compose no geometry, so neither is offered.
    """
    stage = Usd.Stage.Open(str(export_path))
    prim = stage.GetDefaultPrim() if stage else None
    if not prim or not prim.GetVariantSets().HasVariantSet(GEOMETRY_VARIANT_SET):
        return []
    variant_set = prim.GetVariantSet(GEOMETRY_VARIANT_SET)
    found = []
    for variant in variant_set.GetVariantNames():
        variant_set.SetVariantSelection(variant)
        # The pxr stubs omit IsA's schema-class overload.
        if any(d.IsA(UsdGeom.Gprim) for d in Usd.PrimRange(prim)):  # type: ignore
            found.append(variant)
    return found


def item_key(file_path: str, blind_data: bytes | str) -> ItemKey:
    # Houdini returns bytes; its stubs say str.
    if file_path.startswith(JOB_PREFIX):
        relative = PurePosixPath(file_path.removeprefix(JOB_PREFIX))
    else:
        relative = production_relative_path(file_path) or PurePosixPath(file_path)
    return relative.as_posix(), _variant(blind_data)


def _variant(blind_data: bytes | str) -> str:
    try:
        selections = json.loads(blind_data or "{}").get(VARIANTS_BLIND_DATA_KEY, {})
    except (ValueError, AttributeError):
        return ""
    return str(selections.get(GEOMETRY_VARIANT_SET, ""))


def _production_relative(export_path: Path) -> PurePosixPath:
    relative = production_relative_path(export_path)
    if relative is None:
        raise ValueError(f"{export_path} is not under the production folder")
    return relative


__all__ = [
    "VARIANTS_BLIND_DATA_KEY",
    "GalleryItem",
    "ItemKey",
    "geometry_variants",
    "item_key",
    "items_for",
]
