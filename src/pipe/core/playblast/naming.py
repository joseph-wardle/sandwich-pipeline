from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from pipe.core.util.paths import get_edit_path

log = logging.getLogger(__name__)

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
    """Every filename in the directories that exist and can be listed."""
    names: list[str] = []
    for raw_path in directories:
        directory = Path(str(raw_path))
        if not directory.is_dir():
            continue
        try:
            names.extend(item.name for item in directory.iterdir() if item.is_file())
        except OSError:
            # Versioning past a folder we could not read risks overwriting what
            # is in it, so say so rather than silently starting over at v001.
            log.warning("Could not list %s", directory, exc_info=True)
    return names


def build_edit_output_directory(
    department: str, timestamp: datetime | None = None
) -> Path:
    """Return the dated edit-bound output directory for a given department."""
    return get_edit_path() / department / _date_folder(timestamp)


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
    "build_edit_output_directory",
    "existing_filenames",
    "next_versioned_basename",
]
