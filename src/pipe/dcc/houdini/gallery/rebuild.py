"""Regenerate the Asset Gallery from the asset directories on disk.

pipe houdini -p -m pipe.dcc.houdini.gallery.rebuild [--only NAME ...] [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from pipe.core.util.paths import get_production_path
from pipe.core.versioning import (
    DCC_HOUDINI,
    VERSION_MANIFEST_FILENAME,
    load_manifest,
    stream_key_for,
)

from . import copy_db, db, production_db_path, thumbnail, thumbnail_path

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
    keep = {asset.export_path for asset in assets}
    log.info("%d published assets, %d selected", len(assets), len(selected))
    if dry_run:
        for asset in selected:
            verb = "render" if _thumbnail_is_stale(asset, force=force) else "keep"
            log.info("would upsert %s (%s thumbnail)", asset.label, verb)
        if not only:
            for file in db.files(db_path).values():
                if file not in keep:
                    log.info("would drop %s", file)
        return

    thumbnails = {
        asset.export_path: _thumbnail(asset, force=force) for asset in selected
    }

    if db_path.is_file():
        copy_db(db_path, db_path.with_suffix(".db.bak"))
    with db.transaction(db_path) as tx:
        for asset in selected:
            tx.upsert(
                label=asset.label,
                file_path=asset.export_path,
                thumbnail=thumbnails[asset.export_path],
            )
        if not only:
            for label, file in tx.prune(keep):
                log.info("dropped %s -> %s", label, file)
    log.info("Asset Gallery rebuilt: %s", db_path)


def _published_filename(manifest_path: Path) -> str:
    stream = (
        load_manifest(manifest_path).get("streams", {}).get(ASSET_BUILDER_STREAM, {})
    )
    export_path = (stream.get("current") or {}).get("extra", {}).get("export_path")
    if export_path:
        return Path(export_path).name
    return f"{manifest_path.parent.name}.usd"


def _thumbnail_is_stale(asset: PublishedAsset, *, force: bool) -> bool:
    target = thumbnail_path(asset.export_path)
    return (
        force
        or not target.is_file()
        or target.stat().st_mtime < asset.export_path.stat().st_mtime
    )


def _thumbnail(asset: PublishedAsset, *, force: bool) -> bytes | None:
    target = thumbnail_path(asset.export_path)
    if not _thumbnail_is_stale(asset, force=force):
        return target.read_bytes()
    try:
        data = thumbnail.render(asset.export_path, target)
    except thumbnail.ThumbnailRenderError as exc:
        log.warning("No thumbnail for %s: %s", asset.label, exc)
        return None
    log.info("rendered %s", target)
    return data


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
