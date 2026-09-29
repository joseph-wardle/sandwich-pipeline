"""What a version folder of a render layer holds, and which versions can be read.

<layer>/v003/
├── render.usd          what was rendered, with every output's path
├── denoise.json        denoise_batch's config, when the layer is denoised
├── complete            written last, by Cleanup
├── <output>/NNNN.exr   one folder per render product or Cryptomatte filter
└── tmp/                scratch, which Cleanup deletes
"""

from __future__ import annotations

import re
from pathlib import Path

RENDER_USD = "render.usd"
DENOISE_CONFIG = "denoise.json"
COMPLETE = "complete"
TMP = "tmp"
DENOISED = "denoised"
ENCODE = "encode"
LEGACY_DIRS = ("images_dn", "images")

_VERSION = re.compile(r"v(\d+)")


def _numbered(layer: Path) -> list[tuple[int, Path]]:
    found = [
        (int(m[1]), p) for p in layer.glob("v*") if (m := _VERSION.fullmatch(p.name))
    ]
    return sorted(found)


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
