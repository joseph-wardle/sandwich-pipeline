"""Where a shot department's current layer lives on disk."""

from __future__ import annotations

from pathlib import Path

from pipe.core.publish import PUBLISH_DIRNAME


def current_layer_path(
    shot_dir: Path, department: str, name: str | None = None
) -> Path:
    """The layer other departments read. `pipe.core.publish` versions its folder.

    `name` is the layer's name when it isn't the department's, as `anim.spline` is
    for anim's second stream.
    """
    return shot_dir / department / PUBLISH_DIRNAME / f"{name or department}.usd"
