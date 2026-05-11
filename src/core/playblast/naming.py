from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from core.util.util import get_edit_path

log = logging.getLogger(__name__)

_VERSION_PADDING = 3


def next_versioned_basename(
    prefix: str,
    destination_dirs: Iterable[Path | str],
    *,
    now: datetime | None = None,
    padding: int = _VERSION_PADDING,
) -> str:
    """Return a versioned basename `<prefix>_YYYY-MM-DD.v###` whose version
    number is one higher than the highest already present across
    `destination_dirs`."""
    next_version = _next_version(prefix, destination_dirs, now=now)
    return _output_basename(prefix, version=next_version, now=now, padding=padding)


def build_edit_output_directory(
    department: str, timestamp: datetime | None = None
) -> Path:
    """Return the dated edit-bound output directory for a given department."""
    return get_edit_path() / department / _date_folder(timestamp)


def _date_folder(now: datetime | None = None) -> str:
    timestamp = now or datetime.now()
    return timestamp.strftime("%Y-%m-%d")


def _version_token(version: int, *, padding: int = _VERSION_PADDING) -> str:
    if version < 1:
        raise ValueError("Playblast version must be at least 1.")
    return f"v{version:0{padding}d}"


def _output_basename(
    prefix: str,
    *,
    version: int,
    now: datetime | None = None,
    padding: int = _VERSION_PADDING,
) -> str:
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        raise ValueError("Playblast output prefix cannot be empty.")

    day_token = _date_folder(now)
    version_token = _version_token(version, padding=padding)
    return f"{normalized_prefix}_{day_token}.{version_token}"


def _next_version(
    prefix: str,
    destination_dirs: Iterable[Path | str],
    *,
    now: datetime | None = None,
) -> int:
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        raise ValueError("Playblast output prefix cannot be empty.")

    day_token = _date_folder(now)
    version_pattern = _version_pattern(normalized_prefix, day_token)
    highest_version = 0

    for directory in _existing_directories(destination_dirs):
        for version in _versions_in_directory(directory, version_pattern):
            highest_version = max(highest_version, version)

    return highest_version + 1


def _version_pattern(prefix: str, day_token: str) -> re.Pattern[str]:
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
            except ValueError:
                continue
    except OSError:
        log.warning("Could not scan playblast versions in %s", directory, exc_info=True)

    return versions


__all__ = [
    "build_edit_output_directory",
    "next_versioned_basename",
]
