"""One-time migration: rename asset_manifest.json -> version_manifest.json.

Usage
-----
Dry-run (prints what would happen, makes no changes)::

    python -m pipe.versioning.migrate_asset_manifests

Live run::

    python -m pipe.versioning.migrate_asset_manifests --execute

The script walks every directory under the production asset root
(``<production_path>/asset/``), finds each ``asset_manifest.json``, and
renames it to ``version_manifest.json``.

Conflicts (directories that already have both filenames) are reported and
skipped — they are NOT modified.

After this script completes successfully, deploy the code changes that remove
``ASSET_MANIFEST_FILENAME`` and update all references to
``VERSION_MANIFEST_FILENAME``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_OLD_NAME = "asset_manifest.json"
_NEW_NAME = "version_manifest.json"

log = logging.getLogger(__name__)


def _find_asset_root(production_path: Path) -> Path:
    asset_root = production_path / "asset"
    if not asset_root.is_dir():
        raise SystemExit(
            f"Asset root does not exist or is not a directory: {asset_root}"
        )
    return asset_root


def migrate(production_path: Path, *, execute: bool) -> tuple[int, int, int]:
    """Walk the asset root and rename manifest files.

    Returns
    -------
    tuple[int, int, int]
        ``(renamed, skipped_conflict, not_found)`` counts.
    """
    asset_root = _find_asset_root(production_path)
    mode = "LIVE" if execute else "DRY-RUN"
    print(f"[{mode}] Scanning {asset_root}")

    renamed = 0
    skipped_conflict = 0

    for dirpath, _dirnames, filenames in os.walk(asset_root):
        if _OLD_NAME not in filenames:
            continue

        old_path = Path(dirpath) / _OLD_NAME
        new_path = Path(dirpath) / _NEW_NAME

        if new_path.exists():
            print(f"  CONFLICT  {old_path}  (version_manifest.json already exists — skipped)")
            skipped_conflict += 1
            continue

        if execute:
            old_path.rename(new_path)
            print(f"  RENAMED   {old_path}")
        else:
            print(f"  WOULD RENAME  {old_path}")

        renamed += 1

    return renamed, skipped_conflict


def main() -> None:
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(
        description="Rename asset_manifest.json → version_manifest.json across all asset roots."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually rename files. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--production-path",
        default=None,
        help=(
            "Override the production root path. "
            "Defaults to the value of env.production_path."
        ),
    )
    args = parser.parse_args()

    if args.production_path:
        production_path = Path(args.production_path).expanduser().resolve()
    else:
        try:
            from shared.util import get_production_path
            production_path = get_production_path()
        except Exception as exc:
            raise SystemExit(
                f"Could not resolve production path: {exc}\n"
                "Pass --production-path explicitly."
            ) from exc

    renamed, conflicts = migrate(production_path, execute=args.execute)

    print()
    if args.execute:
        print(f"Done. Renamed: {renamed}  Conflicts skipped: {conflicts}")
        if conflicts:
            print(
                "WARNING: Some directories were skipped due to conflicts. "
                "Inspect them manually."
            )
    else:
        print(
            f"Dry-run complete. Would rename: {renamed}  Conflicts: {conflicts}\n"
            "Run with --execute to apply."
        )

    if conflicts:
        sys.exit(1)


if __name__ == "__main__":
    main()
