"""Movies sent to editorial: `edit/shots/<shot>/<shot>_v###_<dept>.mov`."""

from __future__ import annotations

import re
from pathlib import Path

import attrs

from pipe.core.util.paths import get_edit_path

_VERSION_PADDING = 3


@attrs.frozen
class Delivery:
    shot_code: str
    version: int
    department: str
    path: Path


def edit_shot_directory(shot_code: str) -> Path:
    return get_edit_path() / shot_code


def shot_deliveries(shot_code: str) -> tuple[list[Delivery], list[Path]]:
    """The shot's deliveries in version order, and any other files in its folder."""
    directory = edit_shot_directory(shot_code)
    if not directory.is_dir():
        return [], []
    deliveries: list[Delivery] = []
    strays: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        delivery = parse_delivery(path, shot_code)
        if delivery is None:
            strays.append(path)
        else:
            deliveries.append(delivery)
    deliveries.sort(key=lambda d: d.version)
    return deliveries, strays


def parse_delivery(path: Path, shot_code: str) -> Delivery | None:
    # The shot code is given, not parsed: codes like `A_240_PiP` contain
    # underscores of their own.
    match = re.fullmatch(
        rf"{re.escape(shot_code)}_v(?P<version>\d+)_(?P<department>[a-z0-9]+)\.\w+",
        path.name,
    )
    if match is None:
        return None
    return Delivery(
        shot_code, int(match.group("version")), match.group("department"), path
    )


def next_delivery_name(directory: Path, shot_code: str, department: str) -> str:
    """Return `<shot>_v###_<dept>`. A shot has one counter across departments,
    so the number says which delivery arrived last."""
    # Looser than parse_delivery: any file that took a number keeps it taken.
    pattern = re.compile(rf"^{re.escape(shot_code)}_v(?P<version>\d+)_")
    highest_version = 0
    if directory.is_dir():
        for path in directory.iterdir():
            match = pattern.match(path.name)
            if match and path.is_file():
                highest_version = max(highest_version, int(match.group("version")))
    return f"{shot_code}_v{highest_version + 1:0{_VERSION_PADDING}d}_{department}"
