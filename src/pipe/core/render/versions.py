"""A shot's render layers on disk: their versions, frames, which ones comp reads, and deleting them.

shot/<code>/
├── comp/<code>.nk              its Reads name the versions in use
└── render/
    ├── beauty/                 a render layer: any folder that holds a v###
    │   └── v003/
    │       ├── render.usd      what was rendered, with every output's path
    │       ├── denoise.json    denoise_batch's config, when the layer is denoised
    │       ├── complete        written last, by Cleanup
    │       ├── <product>/NNNN.exr
    │       └── tmp/            frames while the job runs, deleted by Cleanup
    ├── FINAL/                  delivered frames, never deleted
    └── fx2d_main/              fx2d's renders, which only FX deletes
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pipe.core.cache import RENDER_DIRNAME

log = logging.getLogger(__name__)

RENDER_USD = "render.usd"
DENOISE_CONFIG = "denoise.json"
COMPLETE = "complete"
TMP = "tmp"
DENOISED = "denoised"
ENCODE = "encode"
# Before Cleanup, versions kept raw frames in images/ and denoised ones in images_dn/.
_LEGACY_DENOISED = "images_dn"
_LEGACY_RAW = "images"
LEGACY_DIRS = (_LEGACY_DENOISED, _LEGACY_RAW)

# The product Render Send denoises.
BEAUTY_PRODUCT = "beauty"
DENOISED_KIND = "denoised"
RAW_KIND = "raw, not denoised yet"
_NOT_PRODUCTS = {TMP, DENOISED, ENCODE, _LEGACY_DENOISED}
# Cryptomattes are IDs, not pictures; Nuke's Cryptomatte node reads them.
_SKIPPED_PRODUCTS = ("crypto",)

_VERSION = re.compile(r"v(\d+)")
_FRAME = re.compile(r"(\d+)\.exr$")
_COMP_DIRNAME = "comp"
# Compared case-insensitively, so a "Final" folder is protected too.
_FINAL_LAYER = "FINAL"
# Mirrors `pipe.dcc.blender.fx2d.util.LAYER_PREFIX`, which needs `bpy` to import.
_FX2D_LAYER_PREFIX = "fx2d_"

# A version's `kept_reasons`, worded to follow "the".
LATEST = "latest"
NEWEST_COMPLETE = "newest complete"
NEWEST_WITH_FRAMES = "newest with frames"


@dataclass(frozen=True)
class Product:
    name: str
    # The best file of each frame: denoised when there is one, else raw.
    frames: dict[int, Path]
    # The version isn't marked complete, so its job may still be writing it.
    rendering: bool
    # DENOISED_KIND or RAW_KIND for each frame of a product the job denoises.
    kinds: dict[int, str]

    @property
    def summary(self) -> str:
        """Such as "40 denoised · 28 raw"; empty for a product nothing denoises."""
        if not self.kinds:
            return ""
        raw = list(self.kinds.values()).count(RAW_KIND)
        if not raw:
            return "denoised"
        return f"{len(self.kinds) - raw} denoised · {raw} raw"


@dataclass(frozen=True)
class RenderVersion:
    path: Path
    is_latest: bool
    # While the latest renders, or after it fails, these are the ones to read.
    is_newest_complete: bool
    is_newest_with_frames: bool
    in_comp: bool
    # Cleanup marked it finished; Nuke's auto-read reads only these.
    is_complete: bool
    # Frames in its folders or in tmp/, so a job still rendering, or stopped
    # before Cleanup, counts.
    has_frames: bool
    modified: datetime

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def kept_reasons(self) -> list[str]:
        """Why Delete All Unused always keeps this version; empty for most."""
        return [
            reason
            for reason, applies in (
                (LATEST, self.is_latest),
                (NEWEST_COMPLETE, self.is_newest_complete),
                (NEWEST_WITH_FRAMES, self.is_newest_with_frames),
            )
            if applies
        ]


@dataclass(frozen=True)
class RenderLayer:
    path: Path
    # Newest first.
    versions: list[RenderVersion]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def protection(self) -> str:
        return protection(self.path)

    @property
    def unused_versions(self) -> list[RenderVersion]:
        """Not read by comp, and none of the latest, newest complete or newest with frames."""
        if self.protection:
            return []
        return [v for v in self.versions if not v.kept_reasons and not v.in_comp]


@dataclass(frozen=True)
class ShotRenders:
    layers: list[RenderLayer]
    comp_script: Path
    # False when the script is missing or unreadable, so no version is `in_comp`.
    comp_read: bool


@dataclass(frozen=True)
class DeleteFailure:
    path: Path
    reason: str


@dataclass(frozen=True)
class DeleteSkipped:
    """Kept because it changed after the artist agreed to delete it."""

    path: Path
    reason: str


def versions(layer: Path) -> list[Path]:
    """The version folders of a render layer, newest first."""
    return [path for _, path in reversed(_numbered(layer)) if path.is_dir()]


def next_version(layer: Path) -> Path:
    taken = _numbered(layer)
    return layer / f"v{(taken[-1][0] if taken else 0) + 1:03}"


def output_dirs(version: Path) -> list[Path]:
    """The folders of a version that hold finished frames, or [] while it has none."""
    if (version / COMPLETE).is_file():
        return sorted(p for p in version.iterdir() if p.is_dir() and p.name != TMP)
    # Versions from before Cleanup marked them complete, and fx2d's deliveries.
    for name in LEGACY_DIRS:
        if (version / name).is_dir():
            return [version / name]
    return []


def protection(layer: Path) -> str:
    """Why the layer's versions are never deleted here, or ""."""
    if layer.name.casefold() == _FINAL_LAYER.casefold():
        return "FINAL layers are delivered frames, and are never deleted."
    if layer.name.startswith(_FX2D_LAYER_PREFIX):
        return "fx2d layers are FX's renders, which only FX deletes."
    return ""


def shot_renders(shot: Path) -> ShotRenders:
    """Reads the disk: every render layer of the shot folder with a version, and FINAL."""
    script = _comp_script(shot)
    used = _versions_read_by(script)
    root = shot / RENDER_DIRNAME
    layers = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        # A linked layer's versions live elsewhere, perhaps in another shot.
        if not folder.is_dir() or folder.is_symlink():
            continue
        found = _versions_of(folder, used or set())
        # FINAL holds delivered frames, often without version folders.
        if found or folder.name.casefold() == _FINAL_LAYER.casefold():
            layers.append(RenderLayer(folder, found))
    return ShotRenders(layers, script, comp_read=used is not None)


def products(version: Path) -> list[Product]:
    """The version's products with frames, each frame denoised where it has been, else raw.

    While its job runs, raw frames wait in tmp/<product>/ and denoised ones in
    tmp/denoised/, which Denoise then moves over the product's frames.
    """
    complete = bool(output_dirs(version))
    # A folder of its own holds the denoised frames, so the product's are raw.
    separate = next(
        (version / n for n in (DENOISED, _LEGACY_DENOISED) if (version / n).is_dir()),
        None,
    )
    denoises = separate is not None or (version / DENOISE_CONFIG).is_file()
    names = sorted(
        {
            folder.name
            for root in (version, version / TMP)
            if root.is_dir()
            for folder in root.iterdir()
            if folder.is_dir() and folder.name not in _NOT_PRODUCTS
        }
    )
    denoised_name = BEAUTY_PRODUCT if BEAUTY_PRODUCT in names else _LEGACY_RAW
    found = []
    for name in names:
        if name.lower().startswith(_SKIPPED_PRODUCTS):
            continue
        denoised = denoises and name == denoised_name
        # Least preferred first, so a better file of a frame replaces it.
        sources = [(version / TMP / name, RAW_KIND)]
        if separate is not None:
            sources.append((version / name, RAW_KIND))
            if denoised:
                sources.append((separate, DENOISED_KIND))
        else:
            sources.append((version / name, DENOISED_KIND))
            if denoised:
                sources.append((version / TMP / DENOISED, DENOISED_KIND))
        frames: dict[int, Path] = {}
        kinds: dict[int, str] = {}
        for folder, kind in sources:
            for frame, path in _frames_in(folder).items():
                frames[frame] = path
                kinds[frame] = kind
        if frames:
            found.append(
                Product(
                    name,
                    dict(sorted(frames.items())),
                    rendering=not complete,
                    kinds=dict(sorted(kinds.items())) if denoised else {},
                )
            )
    return found


def deletion_order(doomed: list[RenderVersion]) -> list[RenderVersion]:
    """Oldest first in each layer, so no deletion changes the `kept_reasons` of
    a version still waiting in the batch."""
    return sorted(doomed, key=lambda v: (v.path.parent, int(v.name.removeprefix("v"))))


def delete_version(version: RenderVersion) -> DeleteFailure | DeleteSkipped | None:
    """Deletes the version folder and its frames, unless it has become protected
    since the artist saw `version`."""
    if reason := protection(version.path.parent):
        return DeleteFailure(version.path, reason)
    if version.path.is_symlink() or version.path.parent.is_symlink():
        return DeleteFailure(version.path, "it is a link to another folder")
    if reason := _newly_protected(version):
        log.warning("Kept %s: %s", version.path, reason)
        return DeleteSkipped(version.path, reason)
    try:
        shutil.rmtree(version.path)
    except OSError as exc:
        log.exception("Could not delete render version %s", version.path)
        return DeleteFailure(version.path, exc.strerror or str(exc))
    log.info("Deleted render version %s", version.path)
    return None


def folder_size(folder: Path) -> int:
    """The bytes the files under `folder` hold, not following links, and
    counting what can't be read as nothing."""
    total = 0
    for root, _dirs, files in os.walk(folder, onerror=lambda _exc: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _numbered(layer: Path) -> list[tuple[int, Path]]:
    found = [
        (int(m[1]), p) for p in layer.glob("v*") if (m := _VERSION.fullmatch(p.name))
    ]
    return sorted(found)


def _frames_in(folder: Path) -> dict[int, Path]:
    if not folder.is_dir():
        return {}
    return {
        int(m[1]): path
        for path in folder.glob("*.exr")
        if (m := _FRAME.search(path.name))
    }


def _comp_script(shot: Path) -> Path:
    return shot / _COMP_DIRNAME / f"{shot.name}.nk"


def _versions_read_by(script: Path) -> set[tuple[str, str]] | None:
    """The (layer, version) pairs any path in `script` names, or None if unreadable.

    Any mention counts, even a disabled Read's: keeping a version too many is
    cheap, deleting one comp needs is not.
    """
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.info("No comp script to check renders against at %s: %s", script, exc)
        return None
    # Below the production root, as Reads spell it through /job, /groups or /cache;
    # not shot/<code>/<dept>/render/, a department's own test renders.
    shot = re.escape(script.parent.parent.name)
    pattern = re.compile(rf"shot/{shot}/{RENDER_DIRNAME}/([^/\n\"{{}}]+)/(v\d+)/")
    return set(pattern.findall(text))


def _versions_of(layer: Path, used: set[tuple[str, str]]) -> list[RenderVersion]:
    newest_first = versions(layer)
    complete = {path: bool(output_dirs(path)) for path in newest_first}
    has_frames = {path: complete[path] or _has_frames(path) for path in newest_first}
    newest_complete = next((p for p in newest_first if complete[p]), None)
    newest_with_frames = next((p for p in newest_first if has_frames[p]), None)
    return [
        RenderVersion(
            path=path,
            is_latest=index == 0,
            is_newest_complete=path == newest_complete,
            is_newest_with_frames=path == newest_with_frames,
            in_comp=(layer.name, path.name) in used,
            is_complete=complete[path],
            has_frames=has_frames[path],
            modified=datetime.fromtimestamp(path.stat().st_mtime),
        )
        for index, path in enumerate(newest_first)
    ]


def _has_frames(version: Path) -> bool:
    """Any frame of a product, in the version or its tmp/, without listing them all."""
    try:
        return any(
            _FRAME.search(exr.name)
            for root in (version, version / TMP)
            if root.is_dir()
            for folder in root.iterdir()
            if folder.is_dir()
            and folder.name != TMP
            and not folder.name.lower().startswith(_SKIPPED_PRODUCTS)
            for exr in folder.glob("*.exr")
        )
    # A folder that can't be read counts as holding frames, which protects it.
    except OSError as exc:
        log.warning("Treating %s as rendered; could not read it: %s", version, exc)
        return True


def _newly_protected(version: RenderVersion) -> str | None:
    """Reads the disk: why `version` should no longer be deleted, if it should not."""
    if not version.path.is_dir():
        return "it is no longer on disk"
    layer = version.path.parent
    # <shot>/render/<layer>/v###
    script = _comp_script(layer.parent.parent)
    used = _versions_read_by(script)
    if used is None and script.exists():
        return f"{script.name} could not be read to check whether it uses this version"
    now = next(
        (v for v in _versions_of(layer, used or set()) if v.path == version.path), None
    )
    if now is None:
        return "it is no longer on disk"
    if now.in_comp and not version.in_comp:
        return f"{script.name} now reads it"
    gained = [r for r in now.kept_reasons if r not in version.kept_reasons]
    return f"it is now the {gained[0]} version" if gained else None
