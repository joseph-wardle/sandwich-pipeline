"""A shot department's current layer on disk, and what its next publish writes."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pipe.core.publish import PUBLISH_DIRNAME, Target, latest_final, next_version
from pipe.core.shotgrid import Shot

from .version_adapter import shot_root_path


def current_layer_path(shot_dir: Path, department: str) -> Path:
    """The layer other departments read. `pipe.core.publish` versions its folder."""
    return shot_dir / department / PUBLISH_DIRNAME / f"{department}.usd"


def published_file_code(shot_code: str, department: str, version: int) -> str:
    """A publish version's code on its ShotGrid PublishedFile, such as `A_050_cfx_v005`."""
    return f"{shot_code}_{department}_v{version:03d}"


def shot_target(shot: Shot, department: str) -> Target:
    """The next version of a shot department's layer."""
    code = cast(str, shot.code)
    current = current_layer_path(shot_root_path(shot), department)
    version = next_version(current)
    return Target(
        entity=shot,
        name=f"{code} {department}",
        current=current,
        version=version,
        file_name=department,
        file_code=published_file_code(code, department, version),
        department=department,
        final=latest_final(current),
    )
