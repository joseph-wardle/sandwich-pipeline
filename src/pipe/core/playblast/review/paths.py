from __future__ import annotations

from pathlib import Path
from typing import Iterable


def default_version_name_from_movie_path(movie_path: Path | str) -> str:
    """Derive a default Version code from the playblast filename stem."""
    return Path(str(movie_path)).stem.strip()


def resolve_preferred_upload_movie_path(
    output_paths: Iterable[Path | str],
    *,
    preferred_paths: Iterable[Path | str] | None = None,
) -> Path | None:
    """Resolve a deterministic movie path for ShotGrid upload.

    Selection order:
    1) first valid file in `preferred_paths`
    2) first valid file in `output_paths`

    A valid file exists on disk and is non-empty.
    """

    normalized_outputs = _normalized_unique_paths(output_paths)
    normalized_preferred = _normalized_unique_paths(preferred_paths or [])

    for path in normalized_preferred:
        if _is_valid_movie_file(path):
            return path

    for path in normalized_outputs:
        if _is_valid_movie_file(path):
            return path

    return None


def _normalized_unique_paths(paths: Iterable[Path | str]) -> list[Path]:
    normalized_paths: list[Path] = []
    seen_paths: set[Path] = set()
    for raw_path in paths:
        path = Path(str(raw_path)).expanduser().resolve()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        normalized_paths.append(path)
    return normalized_paths


def _is_valid_movie_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


__all__ = [
    "default_version_name_from_movie_path",
    "resolve_preferred_upload_movie_path",
]
