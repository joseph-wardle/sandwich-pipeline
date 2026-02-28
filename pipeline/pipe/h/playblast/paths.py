from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shared.util import get_edit_path

from pipe.playblast_naming import (
    playblast_date_folder,
    resolve_versioned_playblast_basename,
)


def build_edit_output_directory(
    department: str, timestamp: datetime | None = None
) -> Path:
    return get_edit_path() / department / playblast_date_folder(timestamp)


def build_output_base_paths(
    department: str,
    shot_code: str,
    custom_dir: Path | None = None,
    timestamp: datetime | None = None,
) -> tuple[Path, Path | None]:
    if timestamp is None:
        timestamp = datetime.now()

    edit_output_dir = build_edit_output_directory(department, timestamp)
    destination_dirs = [edit_output_dir]
    if custom_dir is not None:
        destination_dirs.append(custom_dir)

    output_basename = resolve_versioned_playblast_basename(
        shot_code,
        destination_dirs,
        now=timestamp,
    )

    edit_output_base = edit_output_dir / output_basename
    custom_output_base = (
        custom_dir / output_basename if custom_dir is not None else None
    )
    return edit_output_base, custom_output_base


def build_output_base_path(
    department: str,
    shot_code: str,
    timestamp: datetime | None = None,
) -> Path:
    edit_output_base, _ = build_output_base_paths(
        department,
        shot_code,
        custom_dir=None,
        timestamp=timestamp,
    )
    return edit_output_base


def build_custom_output_base_path(
    custom_dir: Path,
    shot_code: str,
    timestamp: datetime | None = None,
) -> Path:
    output_basename = resolve_versioned_playblast_basename(
        shot_code,
        [custom_dir],
        now=timestamp,
    )
    return custom_dir / output_basename
