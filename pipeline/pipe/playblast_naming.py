from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

PLAYBLAST_VERSION_PADDING = 3


def playblast_date_folder(now: datetime | None = None) -> str:
    """Return the ISO date folder token used by playblast exports."""
    timestamp = now or datetime.now()
    return timestamp.strftime("%Y-%m-%d")


def playblast_version_token(
    version: int, *, padding: int = PLAYBLAST_VERSION_PADDING
) -> str:
    """Return a normalized version token like 'v001'."""
    if version < 1:
        raise ValueError("Playblast version must be at least 1.")
    return f"v{version:0{padding}d}"


def playblast_output_basename(
    prefix: str,
    *,
    version: int,
    now: datetime | None = None,
    padding: int = PLAYBLAST_VERSION_PADDING,
) -> str:
    """Build a final playblast basename as '<prefix>_YYYY-MM-DD.v###'."""
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        raise ValueError("Playblast output prefix cannot be empty.")

    day_token = playblast_date_folder(now)
    version_token = playblast_version_token(version, padding=padding)
    return f"{normalized_prefix}_{day_token}.{version_token}"


def next_playblast_version(
    prefix: str,
    destination_dirs: Iterable[Path | str],
    *,
    now: datetime | None = None,
) -> int:
    """Return the next available version number across destination folders."""
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        raise ValueError("Playblast output prefix cannot be empty.")

    day_token = playblast_date_folder(now)
    version_pattern = _playblast_version_pattern(normalized_prefix, day_token)
    highest_version = 0

    for directory in _existing_directories(destination_dirs):
        for version in _versions_in_directory(directory, version_pattern):
            highest_version = max(highest_version, version)

    return highest_version + 1


def resolve_versioned_playblast_basename(
    prefix: str,
    destination_dirs: Iterable[Path | str],
    *,
    now: datetime | None = None,
    padding: int = PLAYBLAST_VERSION_PADDING,
) -> str:
    """Resolve a new basename using the next version across all destinations."""
    next_version = next_playblast_version(prefix, destination_dirs, now=now)
    return playblast_output_basename(
        prefix,
        version=next_version,
        now=now,
        padding=padding,
    )


def _playblast_version_pattern(prefix: str, day_token: str) -> re.Pattern[str]:
    escaped_prefix = re.escape(prefix)
    escaped_day_token = re.escape(day_token)
    return re.compile(
        rf"^{escaped_prefix}_{escaped_day_token}\.v(?P<version>\d+)(?:\..+)?$"
    )


def _existing_directories(destination_dirs: Iterable[Path | str]) -> list[Path]:
    directories: list[Path] = []
    for raw_path in destination_dirs:
        path = Path(str(raw_path))
        if path.exists() and path.is_dir():
            directories.append(path)
    return directories


def _versions_in_directory(directory: Path, pattern: re.Pattern[str]) -> list[int]:
    versions: list[int] = []
    try:
        for item in directory.iterdir():
            if not item.is_file():
                continue
            match = pattern.match(item.name)
            if not match:
                continue
            try:
                versions.append(int(match.group("version")))
            except Exception:
                continue
    except Exception:
        log.warning("Could not scan playblast versions in %s", directory, exc_info=True)

    return versions
