"""A publish folder's immutable versions, and the current layer that points at one.

publish/
├── <name>.usda         current: sublayers ./v002/<name>.usd
├── v001/
├── v002/
│   ├── <name>.usd      holds who published it, when and why
│   └── _src/           the scene file that made it
└── .v003.tmp/          a version being written

Every function but `pin` takes the current layer's path, which names the folder
and the version layers in it.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pxr import Sdf, Tf, UsdUtils

PUBLISH_DIRNAME = "publish"
SOURCE_DIRNAME = "_src"
AUTHOR_KEY = "author"
DATE_KEY = "date"
NOTE_KEY = "note"

_VERSION = re.compile(r"v([0-9]{3,})")
_USD_SUFFIXES = (".usd", ".usda", ".usdc")
# What a stage takes from its root layer alone. The current layer carries them so
# that opening it gives the same units, frame range and frame rate as the version.
_STAGE_INFO_KEYS = (
    "metersPerUnit",
    "upAxis",
    "startTimeCode",
    "endTimeCode",
    "timeCodesPerSecond",
    "framesPerSecond",
)


@dataclass(frozen=True)
class VersionInfo:
    version: int
    author: str
    date: datetime
    note: str
    # The scene file in `_src/`; None for a version from before scenes were kept.
    source: Path | None


def version_layer_path(current: Path, version: int) -> Path:
    return current.parent / _version_dirname(version) / f"{current.stem}.usd"


def staging_layer_path(current: Path, version: int) -> Path:
    """Where a version is written before it is committed.

    A sibling of the version directory, so the relative asset paths written into
    the layer stay valid when it is renamed.
    """
    staging = f".{_version_dirname(version)}.tmp"
    return current.parent / staging / f"{current.stem}.usd"


def create_staging(current: Path, version: int) -> Path:
    """Create the empty folder a version is written into; return its layer path.

    Raises:
        FileExistsError: Another publish of `version` is running, or one stopped
            partway and left its folder behind.
    """
    staging = staging_layer_path(current, version).parent
    staging.parent.mkdir(parents=True, exist_ok=True)
    # Never reused: whatever is already in it would be committed with this version.
    staging.mkdir()
    return staging_layer_path(current, version)


def next_version(current: Path) -> int:
    return max(_numbered(current), default=0) + 1


def versions(current: Path) -> list[int]:
    """The published versions, newest first."""
    published = [
        version
        for version in _numbered(current)
        if version_layer_path(current, version).is_file()
    ]
    return sorted(published, reverse=True)


def current_version(current: Path) -> int | None:
    """The version `current` points at, or None if it isn't a current layer.

    Read from disk: USD's layer cache keeps whatever the session opened earlier,
    which is the previous version once someone has published.
    """
    layer = Sdf.Layer.OpenAsAnonymous(str(current), metadataOnly=True)
    return None if layer is None else _points_at(layer, current)


def loaded_version(current: Path) -> int | None:
    """The version this session's copy of `current` points at, which its stages show.

    None if the session hasn't opened `current`, or it isn't a current layer.
    """
    layer = Sdf.Layer.Find(str(current))
    return None if layer is None else _points_at(layer, current)


def version_info(current: Path, version: int) -> VersionInfo:
    path = version_layer_path(current, version)
    layer = Sdf.Layer.OpenAsAnonymous(str(path), metadataOnly=True)
    data = layer.customLayerData
    if DATE_KEY in data:
        date = datetime.fromisoformat(data[DATE_KEY])
    else:
        # A version from before publishes were stamped has only its file's date.
        date = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    return VersionInfo(
        version=version,
        author=data.get(AUTHOR_KEY, ""),
        date=date,
        note=data.get(NOTE_KEY, ""),
        source=_source(current, version),
    )


def scene_version(current: Path, scene: Path) -> int | None:
    """The newest version that `scene`, as it is on disk, made."""
    if not scene.is_file():
        return None
    for version in versions(current):
        source = _source(current, version)
        # Equal sizes and dates settle it without reading either file.
        if source is not None and filecmp.cmp(scene, source):
            return version
    return None


def copy_source(current: Path, version: int, source: Path) -> Path:
    """Copy the scene that made a staged version into its `_src/`."""
    folder = staging_layer_path(current, version).parent / SOURCE_DIRNAME
    folder.mkdir()
    return Path(shutil.copy2(source, folder / source.name))


def stamp(current: Path, version: int, *, author: str, note: str) -> None:
    """Record on a staged version's layer who published it, when and why."""
    layer = Sdf.Layer.FindOrOpen(str(staging_layer_path(current, version)))
    layer.customLayerData = {
        **layer.customLayerData,
        AUTHOR_KEY: author,
        DATE_KEY: datetime.now().astimezone().isoformat(timespec="seconds"),
        NOTE_KEY: note,
    }
    layer.Save()


def discard_staged(current: Path, version: int) -> None:
    shutil.rmtree(staging_layer_path(current, version).parent)


def commit_version(current: Path, version: int) -> Path:
    """Rename the staged version into place. It is read once it is made current."""
    staging = staging_layer_path(current, version).parent
    staging.rename(version_layer_path(current, version).parent)
    return version_layer_path(current, version)


def make_current(current: Path, version: int) -> None:
    """Point the current layer at `version`. Also how a publish is rolled back.

    Raises:
        FileNotFoundError: `version` was never published.
        OSError: The current layer couldn't be written.
    """
    version_path = version_layer_path(current, version)
    version_layer = Sdf.Layer.FindOrOpen(str(version_path))
    if version_layer is None:
        raise FileNotFoundError(f"There is no version {version}: {version_path}")

    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.subLayerPaths.append(_sublayer(current, version))
    if version_layer.defaultPrim:
        layer.defaultPrim = version_layer.defaultPrim
    for key in _STAGE_INFO_KEYS:
        if version_layer.pseudoRoot.HasInfo(key):
            layer.pseudoRoot.SetInfo(key, version_layer.pseudoRoot.GetInfo(key))

    # Written beside the current layer and swapped in, so a reader opening it
    # mid-publish gets either the old current layer or the new one, never half.
    temp = current.with_name(f".{current.stem}.tmp{current.suffix}")
    # Text even when named `.usd`, so the version it points at can be read with cat.
    try:
        layer.Export(str(temp), args={"format": "usda"})
    except Tf.ErrorException as exc:
        # USD has its own error for a file it can't write.
        raise OSError(f"USD couldn't write {temp}.") from exc
    os.replace(temp, current)


def pin(
    layer: Sdf.Layer, version_of: Callable[[Path], int | None] = current_version
) -> None:
    """Point every path in `layer` that names a current layer at the version behind it.

    `version_of` says which version that is. A caller pinning several layers
    passes one that remembers its answers, so a publish that lands between two
    of them can't give them different versions.
    """

    def pinned(path: str) -> str:
        current = Path(layer.ComputeAbsolutePath(path))
        # Only a USD file in a publish folder can be a current layer. Most paths
        # are textures and caches, which can't be opened to find out.
        in_publish_folder = current.parent.name == PUBLISH_DIRNAME
        if not in_publish_folder or current.suffix not in _USD_SUFFIXES:
            return path
        version = version_of(current)
        if version is None:
            return path
        return str(version_layer_path(current, version))

    UsdUtils.ModifyAssetPaths(layer, pinned)


def _numbered(current: Path) -> list[int]:
    """Every `v###` folder's number. One without its layer still takes the number."""
    if not current.parent.is_dir():
        return []
    return [
        int(match.group(1))
        for entry in current.parent.iterdir()
        if entry.is_dir() and (match := _VERSION.fullmatch(entry.name))
    ]


def _points_at(layer: Sdf.Layer, current: Path) -> int | None:
    """The version `layer`, a copy of `current`, sublayers; None if it isn't a current layer."""
    if len(layer.subLayerPaths) != 1:
        return None
    sublayer = str(layer.subLayerPaths[0])
    match = _VERSION.fullmatch(Path(sublayer).parent.name)
    if match is None:
        return None
    version = int(match.group(1))
    return version if sublayer == _sublayer(current, version) else None


def _source(current: Path, version: int) -> Path | None:
    folder = current.parent / _version_dirname(version) / SOURCE_DIRNAME
    return next(folder.glob("*"), None)


def _sublayer(current: Path, version: int) -> str:
    return f"./{_version_dirname(version)}/{current.stem}.usd"


def _version_dirname(version: int) -> str:
    return f"v{version:03d}"
