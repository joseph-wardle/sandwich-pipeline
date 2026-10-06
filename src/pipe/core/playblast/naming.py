from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from pipe.core.util.paths import get_edit_path

_VERSION_PADDING = 3


def next_versioned_basename(
    prefix: str,
    occupied_names: Iterable[str],
    *,
    now: datetime | None = None,
) -> str:
    """Return `<prefix>_YYYY-MM-DD.v###`, one past the highest version in
    `occupied_names`."""
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        raise ValueError("Playblast output prefix cannot be empty.")

    day_token = _date_folder(now)
    pattern = _version_pattern(normalized_prefix, day_token)

    highest_version = 0
    for name in occupied_names:
        match = pattern.match(name)
        if match:
            highest_version = max(highest_version, int(match.group("version")))

    version_token = f"v{highest_version + 1:0{_VERSION_PADDING}d}"
    return f"{normalized_prefix}_{day_token}.{version_token}"


def existing_filenames(directories: Iterable[Path | str]) -> list[str]:
    """Every filename in the directories that exist."""
    names: list[str] = []
    for raw_path in directories:
        directory = Path(str(raw_path))
        if not directory.is_dir():
            continue
        names.extend(item.name for item in directory.iterdir() if item.is_file())
    return names


def edit_shot_directory(shot_code: str) -> Path:
    return get_edit_path() / shot_code


def next_delivery_name(directory: Path, shot_code: str, department: str) -> str:
    """Return `<shot>_v###_<dept>`. A shot has one counter across departments,
    so the number says which delivery arrived last."""
    pattern = re.compile(rf"^{re.escape(shot_code)}_v(?P<version>\d+)_")
    highest_version = 0
    for name in existing_filenames([directory]):
        match = pattern.match(name)
        if match:
            highest_version = max(highest_version, int(match.group("version")))
    return f"{shot_code}_v{highest_version + 1:0{_VERSION_PADDING}d}_{department}"


def _date_folder(now: datetime | None = None) -> str:
    timestamp = now or datetime.now()
    return timestamp.strftime("%Y-%m-%d")


def _version_pattern(prefix: str, day_token: str) -> re.Pattern[str]:
    escaped_prefix = re.escape(prefix)
    escaped_day_token = re.escape(day_token)
    return re.compile(
        rf"^{escaped_prefix}_{escaped_day_token}\.v(?P<version>\d+)(?:\..+)?$"
    )


__all__ = [
    "edit_shot_directory",
    "existing_filenames",
    "next_delivery_name",
    "next_versioned_basename",
]
