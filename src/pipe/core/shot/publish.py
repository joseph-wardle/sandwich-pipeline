"""A shot department's current layer on disk, and its versions' names in ShotGrid."""

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


def published_file_code(shot_code: str, name: str, version: int) -> str:
    """A publish version's code on its ShotGrid PublishedFile, such as `A_050_cfx_v005`.

    `name` is the layer's name, which is also the PublishedFile's name.
    """
    return f"{shot_code}_{name}_v{version:03d}"
