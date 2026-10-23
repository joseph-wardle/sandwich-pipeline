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
from pipe.core.render import output_dirs, versions
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


@dataclass(frozen=True)
class RenderVersion:
    path: Path
    is_latest: bool
    in_comp: bool
    has_frames: bool
    modified: datetime

    @property
    def name(self) -> str:
        return self.path.name


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
        """Neither read by the comp nor the latest, which comp may not have read yet.

        Without a comp script, that is every version but the latest.
        """
        if self.is_final:
            return []
        return [
            version
            for version in self.versions
            if not version.is_latest and not version.in_comp
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


def is_final_layer(layer: Path) -> bool:
    return layer.name.casefold() == _FINAL_LAYER.casefold()


def render_root(shot: Shot) -> Path:
    # Not resolved: the artist should see the production path, not the cache.
    return get_production_path() / shot.path / RENDER_DIRNAME


def comp_script(shot: Shot) -> Path:
    return get_production_path() / shot.path / _COMP_DIRNAME / f"{shot.code}.nk"


def find_renders(shot: Shot) -> ShotRenders:
    """Reads the disk: every render layer of `shot` with a version, and FINAL."""
    script = comp_script(shot)
    used = _versions_read_by(script, shot)
    root = render_root(shot)
    layers = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith(_FX2D_LAYER_PREFIX):
                continue
            found = _versions_of(folder, used or set())
            # FINAL holds delivered frames, often without version folders.
            if found or is_final_layer(folder):
                layers.append(RenderLayer(folder, found))
    return ShotRenders(layers, script, comp_read=used is not None)


def _versions_read_by(script: Path, shot: Shot) -> set[tuple[str, str]] | None:
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
        rf"shot/{re.escape(shot.code or '')}/{RENDER_DIRNAME}/([^/\n\"{{}}]+)/(v\d+)/"
    )
    return set(pattern.findall(text))


def _versions_of(layer: Path, used: set[tuple[str, str]]) -> list[RenderVersion]:
    newest_first = versions(layer)
    return [
        RenderVersion(
            path=path,
            is_latest=index == 0,
            in_comp=(layer.name, path.name) in used,
            has_frames=bool(output_dirs(path)),
            modified=datetime.fromtimestamp(path.stat().st_mtime),
        )
        for index, path in enumerate(newest_first)
    ]


def delete_version(version: RenderVersion) -> DeleteFailure | None:
    """Deletes the version folder and every frame in it. Returns why it could not."""
    if is_final_layer(version.path.parent):
        log.error("Refused to delete %s, which is in a FINAL layer", version.path)
        return DeleteFailure(version.path, "FINAL layers are never deleted")
    try:
        shutil.rmtree(version.path)
    except OSError as exc:
        log.exception("Could not delete render version %s", version.path)
        return DeleteFailure(version.path, exc.strerror or str(exc))
    log.info("Deleted render version %s", version.path)
    return None
