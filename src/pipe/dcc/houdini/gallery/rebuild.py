"""Regenerate the Asset Gallery from the asset directories on disk.

pipe houdini -p -m pipe.dcc.houdini.gallery.rebuild [--only NAME ...] [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from pipe.core.util.paths import get_production_path
from pipe.core.versioning import (
    DCC_HOUDINI,
    VERSION_MANIFEST_FILENAME,
    load_manifest,
    stream_key_for,
)

from . import copy_db, db, production_db_path, thumbnail
from .items import GalleryItem, items_for

log = logging.getLogger(__name__)

ASSET_BUILDER_STREAM = stream_key_for(DCC_HOUDINI, "asset_builder", "hipnc")


@dataclass(frozen=True)
class PublishedAsset:
    label: str
    export_path: Path


def inventory(asset_root: Path) -> list[PublishedAsset]:
    """Every asset directory whose published component exists on disk."""
    manifests = sorted(
        [
            *asset_root.glob(f"*/{VERSION_MANIFEST_FILENAME}"),
            *asset_root.glob(f"*/*/{VERSION_MANIFEST_FILENAME}"),
        ]
    )
    assets: list[PublishedAsset] = []
    for manifest_path in manifests:
        asset_dir = manifest_path.parent
        if any(
            part.startswith(".") for part in asset_dir.relative_to(asset_root).parts
        ):
            continue
        export_path = asset_dir / "publish" / _published_filename(manifest_path)
        if not export_path.is_file():
            log.info("Skipping %s: no published component", asset_dir)
            continue
        assets.append(PublishedAsset(label=asset_dir.name, export_path=export_path))
    return assets


def rebuild(*, only: set[str], force: bool, dry_run: bool) -> None:
    db_path = production_db_path()
    assets = inventory(get_production_path() / "asset")
    selected = [asset for asset in assets if not only or asset.label in only]
    items = [
        item for asset in selected for item in items_for(asset.label, asset.export_path)
    ]
    keep = {item.key for item in items}
    # `--only` leaves every other asset's entries alone.
    within = {item.key[0] for item in items} if only else None
    log.info(
        "%d published assets, %d selected, %d gallery items",
        len(assets),
        len(selected),
        len(items),
    )
    if dry_run:
        for item in items:
            verb = "render" if _thumbnail_is_stale(item, force=force) else "keep"
            log.info("would upsert %s (%s thumbnail)", item.label, verb)
        for key in db.keys(db_path).values():
            if key not in keep and (within is None or key[0] in within):
                log.info("would drop %s %s", *key)
        return

    thumbnails = _thumbnails(items, force=force)

    if db_path.is_file():
        copy_db(db_path, db_path.with_suffix(".db.bak"))
    with db.transaction(db_path) as tx:
        for item in items:
            tx.upsert(item, thumbnails[item])
        for label, file in tx.prune(keep, within=within):
            log.info("dropped %s -> %s", label, file)
    log.info("Asset Gallery rebuilt: %s", db_path)


def _published_filename(manifest_path: Path) -> str:
    stream = (
        load_manifest(manifest_path).get("streams", {}).get(ASSET_BUILDER_STREAM, {})
    )
    export_path = (stream.get("current") or {}).get("extra", {}).get("export_path")
    if export_path:
        return PureWindowsPath(export_path).name
    return f"{manifest_path.parent.name}.usd"


def _thumbnail_is_stale(item: GalleryItem, *, force: bool) -> bool:
    target = item.thumbnail_path
    return (
        force
        or not target.is_file()
        or target.stat().st_mtime < item.export_path.stat().st_mtime
    )


def _thumbnails(
    items: list[GalleryItem], *, force: bool
) -> dict[GalleryItem, bytes | None]:
    """Each item's thumbnail, rendering only those older than their publish."""
    stale = [item for item in items if _thumbnail_is_stale(item, force=force)]
    rendered = thumbnail.render_all(stale)
    thumbnails: dict[GalleryItem, bytes | None] = {}
    for item in items:
        result = rendered.get(item)
        if result is None:
            thumbnails[item] = item.thumbnail_path.read_bytes()
        elif isinstance(result, thumbnail.ThumbnailRenderError):
            log.warning("No thumbnail for %s: %s", item.label, result)
            thumbnails[item] = None
        else:
            log.info("rendered %s", item.thumbnail_path)
            thumbnails[item] = result
    return thumbnails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--only", nargs="+", default=[], metavar="NAME", help="asset directory names"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-render every thumbnail"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change and stop"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rebuild(only=set(args.only), force=args.force, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
