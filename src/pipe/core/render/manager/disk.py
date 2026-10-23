"""The render layers of a shot, which versions its comp reads, and deleting them.

shot/<code>/
├── comp/<code>.nk             its Reads name the versions that are in use
└── render/                    a symlink into the cache mount
    ├── beauty/                a layer: any folder that holds a `v###`
    │   ├── v001/
    │   └── v002/              the latest, which bulk cleanup always keeps
    ├── FINAL/                 delivered frames, which are never deleted
    └── fx2d_main/             fx2d's deliveries, which are not the lighter's
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pipe.core.cache import RENDER_DIRNAME
from pipe.core.render import TMP, output_dirs, versions
from pipe.core.shotgrid import Shot
from pipe.core.util.paths import get_production_path

log = logging.getLogger(__name__)

# Blender fx2d renders its layers into the same folder; an FX artist owns
# those, so a lighter's cleanup never lists them. Mirrors
# `pipe.dcc.blender.fx2d.util.LAYER_PREFIX`, which needs `bpy` to import.
_FX2D_LAYER_PREFIX = "fx2d_"
_COMP_DIRNAME = "comp"
# Compared case-insensitively, so a "Final" folder is protected too.
_FINAL_LAYER = "FINAL"

# A version's `kept_reasons`, worded to follow "the".
LATEST = "latest"
NEWEST_COMPLETE = "newest complete"
NEWEST_WITH_FRAMES = "newest with frames"


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
    # Any frame on disk, so a job that stopped before Cleanup still counts.
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
    versions: list[RenderVersion]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_final(self) -> bool:
        return is_final_layer(self.path)

    @property
    def unused_versions(self) -> list[RenderVersion]:
        """Not read by the comp, and with no `kept_reasons`: not the latest
        (which comp may not have read yet), nor the newest complete or newest
        with frames (what to read while the latest renders, or after it fails).

        Without a comp script, that is every version without `kept_reasons`.
        """
        if self.is_final:
            return []
        return [
            version
            for version in self.versions
            if not version.kept_reasons and not version.in_comp
        ]


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


def is_final_layer(layer: Path) -> bool:
    return layer.name.casefold() == _FINAL_LAYER.casefold()


def render_root(shot: Shot) -> Path:
    # Not resolved: the artist should see the production path, not the cache.
    return get_production_path() / shot.path / RENDER_DIRNAME


def comp_script(shot: Shot) -> Path:
    return _comp_script_in(get_production_path() / shot.path)


def _comp_script_in(shot_dir: Path) -> Path:
    return shot_dir / _COMP_DIRNAME / f"{shot_dir.name}.nk"


def find_renders(shot: Shot) -> ShotRenders:
    """Reads the disk: every render layer of `shot` with a version, and FINAL."""
    script = comp_script(shot)
    used = _versions_read_by(script, shot.code or "")
    root = render_root(shot)
    layers = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith(_FX2D_LAYER_PREFIX):
                continue
            # A linked layer's versions live elsewhere, perhaps in another shot.
            if folder.is_symlink():
                log.info("Skipping %s, which links to %s", folder, folder.readlink())
                continue
            found = _versions_of(folder, used or set())
            # FINAL holds delivered frames, often without version folders.
            if found or is_final_layer(folder):
                layers.append(RenderLayer(folder, found))
    return ShotRenders(layers, script, comp_read=used is not None)


def _versions_read_by(script: Path, shot_code: str) -> set[tuple[str, str]] | None:
    """The (layer, version) pairs any path in `script` names, or None if unreadable.

    Matches the path below the production root, since Reads spell it through
    /job, /groups, or the /cache link. Any mention counts, even a disabled
    Read's: keeping a version too many is cheap, deleting one comp needs is not.
    """
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.info("No comp script to check renders against at %s: %s", script, exc)
        return None
    # Not `shot/<code>/<dept>/render/`, which is a department's own test renders.
    pattern = re.compile(
        rf"shot/{re.escape(shot_code)}/{RENDER_DIRNAME}/([^/\n\"{{}}]+)/(v\d+)/"
    )
    return set(pattern.findall(text))


def _versions_of(layer: Path, used: set[tuple[str, str]]) -> list[RenderVersion]:
    newest_first = versions(layer)
    complete = {path: bool(output_dirs(path)) for path in newest_first}
    has_frames = {path: complete[path] or _holds_frames(path) for path in newest_first}
    newest_complete = next((path for path in newest_first if complete[path]), None)
    newest_with_frames = next((path for path in newest_first if has_frames[path]), None)
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


def _holds_frames(version: Path) -> bool:
    """Any file in an output folder, finished or not; `tmp/` is only scratch.

    A folder that cannot be read counts as holding frames, which protects it.
    """
    try:
        return any(
            child.is_file()
            for folder in version.iterdir()
            if folder.is_dir() and folder.name != TMP
            for child in folder.iterdir()
        )
    except OSError as exc:
        log.warning("Treating %s as rendered; could not read it: %s", version, exc)
        return True


def deletion_order(doomed: list[RenderVersion]) -> list[RenderVersion]:
    """Oldest first in each layer, the order `delete_version` expects.

    Deleting a version can only change a newer one's `kept_reasons`. Oldest
    first, no deletion changes them for a version still waiting in the batch.
    """
    return sorted(
        doomed,
        key=lambda version: (version.path.parent, int(version.name.removeprefix("v"))),
    )


def delete_version(version: RenderVersion) -> DeleteFailure | DeleteSkipped | None:
    """Deletes the version folder and every frame in it, unless it is now protected.

    `version` is what the artist saw when they agreed to delete it. The comp
    script and the layer are read again first, and a version that has since
    become read by the comp, or gained one of its `kept_reasons`, is kept.
    """
    layer = version.path.parent
    if is_final_layer(layer):
        log.error("Refused to delete %s, which is in a FINAL layer", version.path)
        return DeleteFailure(version.path, "FINAL layers are never deleted")
    # `find_renders` never lists these; a version reaching here from one is a bug.
    if layer.is_symlink():
        log.error("Refused to delete %s, whose layer folder is a link", version.path)
        return DeleteFailure(version.path, "its layer folder is a link")
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


def _newly_protected(version: RenderVersion) -> str | None:
    """Reads the disk: why `version` should no longer be deleted, if it should not."""
    if not version.path.is_dir():
        return "it is no longer on disk"
    layer = version.path.parent
    # <shot>/render/<layer>/v###
    script = _comp_script_in(layer.parent.parent)
    used = _versions_read_by(script, layer.parent.parent.name)
    if used is None and script.exists():
        return f"{script.name} could not be read to check whether it uses this version"
    now = next(
        (
            found
            for found in _versions_of(layer, used or set())
            if found.path == version.path
        ),
        None,
    )
    if now is None:
        return "it is no longer on disk"
    if now.in_comp and not version.in_comp:
        return f"{script.name} now reads it"
    gained = [r for r in now.kept_reasons if r not in version.kept_reasons]
    return f"it is now the {gained[0]} version" if gained else None
