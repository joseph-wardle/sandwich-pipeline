"""Rules for naming a new asset, and what a name would claim in ShotGrid and on disk."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pipe.core.asset.paths import DEFAULT_GEOMETRY_VARIANT, asset_root_from_path
from pipe.core.shotgrid import Asset
from pipe.core.shotgrid.paths import build_asset_path, normalize_display_name

_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
_SUBDIRECTORY_RE = re.compile(r"[a-z0-9_]+")

_RENAME = "Choose a different name."


@dataclass(frozen=True)
class New:
    """No asset holds the name, so creating one is safe."""

    display_name: str
    subdirectory: str | None
    rigged: bool = False


@dataclass(frozen=True)
class Adopt:
    """The only record holding the name has no files yet, so nothing is overwritten."""

    asset: Asset
    variant: str


@dataclass(frozen=True)
class Occupied:
    """The name is held by work a new asset must not touch."""

    reason: str


def name_problem(display_name: str) -> str | None:
    """Why `display_name` cannot name an asset, or `None` if it can."""
    name = normalize_display_name(display_name)
    if _NAME_RE.fullmatch(name):
        return None
    if not name:
        return "Type a name with at least one letter in it."
    return f'"{display_name}" becomes "{name}", which must start with a letter.'


def subdirectories_in_use(assets: Iterable[Asset]) -> set[str]:
    return {asset.subdirectory for asset in assets if asset.subdirectory}


def subdirectory_problem(subdirectory: str, in_use: set[str]) -> str | None:
    """Why a new asset cannot go in `subdirectory`, or `None` if it can.

    Only new folders are judged; one an asset already uses stays usable as spelled.
    """
    if subdirectory in in_use or _SUBDIRECTORY_RE.fullmatch(subdirectory):
        return None
    return (
        f'"{subdirectory}" would be a new folder, and a new folder may only use '
        "lowercase letters, digits and underscores."
    )


def classify(
    display_name: str,
    subdirectory: str | None,
    assets: Iterable[Asset],
    production_root: Path | None = None,
) -> New | Adopt | Occupied:
    """What `display_name` would claim among `assets`, the rows of `find_assets()`."""
    name = normalize_display_name(display_name)
    holders = [asset for asset in assets if asset.name == name]
    if len(holders) > 1:
        codes = ", ".join(sorted(asset.display_name for asset in holders))
        return Occupied(
            f'{len(holders)} ShotGrid assets are named "{name}" ({codes}). {_RENAME}'
        )

    if not holders:
        path = build_asset_path(display_name, subdirectory)
        if _holds_files(asset_root_from_path(path, production_root)):
            return Occupied(f"{path} holds files but no ShotGrid asset. {_RENAME}")
        return New(display_name, subdirectory)

    (asset,) = holders
    variants = sorted(asset.geometry_variants or ())
    if len(variants) > 1:
        return Occupied(
            f'The ShotGrid asset "{asset.display_name}" has geometry variants '
            f"{', '.join(variants)}. {_RENAME}"
        )
    if _holds_files(asset_root_from_path(asset.asset_path, production_root)):
        return Occupied(
            f'The ShotGrid asset "{asset.display_name}" already has files in '
            f"{asset.asset_path}. {_RENAME}"
        )
    return Adopt(asset, variants[0] if variants else DEFAULT_GEOMETRY_VARIANT)


def _holds_files(root: Path) -> bool:
    return any(path.is_file() for path in root.rglob("*"))
