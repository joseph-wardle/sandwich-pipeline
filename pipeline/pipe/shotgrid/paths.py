"""Pure path and name canonicalization for ShotGrid entities.

These helpers contain no ShotGrid imports — they take the strings that the
client produces and turn them into the relative paths the rest of the pipeline
expects on disk.  Stay-pure: no DCC imports, no I/O.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_display_name(name: str | None) -> str:
    """Normalize a ShotGrid display name into a pipeline-safe identifier.

    Steps: unicode normalize → encode ASCII → lowercase → spaces to underscores
    → strip non-alphanumeric characters.
    """
    if not name:
        return ""
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    normalized_name = ascii_name.strip().lower().replace(" ", "_")
    normalized_name = re.sub(r"[^a-z0-9_]", "", normalized_name)
    return normalized_name


def normalize_subdirectory(subdirectory: str | None) -> str | None:
    """Normalize and validate an asset subdirectory token.

    The subdirectory must be a single folder name (no path separators).
    """
    if subdirectory is None:
        return None
    normalized = str(subdirectory).strip()
    if not normalized:
        return None
    if normalized in {".", ".."}:
        raise ValueError("Asset subdirectory cannot be '.' or '..'")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(
            "Asset subdirectory must be a single folder name without path separators"
        )
    return normalized


def build_asset_path(display_name: str | None, subdirectory: str | None) -> str:
    """Build the canonical relative asset path.

    Result format: `asset/<optional-subdirectory>/<normalized-asset-name>`
    """
    asset_name = normalize_display_name(display_name) or "asset"
    path_parts = ["asset"]
    normalized_subdirectory = normalize_subdirectory(subdirectory)
    if normalized_subdirectory:
        path_parts.append(normalized_subdirectory)
    path_parts.append(asset_name)
    return "/".join(path_parts)


def build_environment_path(display_name: str | None, subdirectory: str | None) -> str:
    """Build the canonical relative environment path.

    Result format: `set/<optional-subdirectory>/<normalized-environment-name>`
    """
    env_name = normalize_display_name(display_name) or "set"
    path_parts = ["set"]
    normalized_subdirectory = normalize_subdirectory(subdirectory)
    if normalized_subdirectory:
        path_parts.append(normalized_subdirectory)
    path_parts.append(env_name)
    return "/".join(path_parts)


def validate_shot_code_token(shot_code: str | None) -> str:
    """Validate a shot code for safe use as a single path token.

    Rules: required, non-empty, not `.` or `..`, no path separators.
    """
    if shot_code is None:
        raise ValueError("Shot code is required")

    token = str(shot_code).strip()
    if not token:
        raise ValueError("Shot code cannot be empty")
    if token in {".", ".."}:
        raise ValueError("Shot code cannot be '.' or '..'")
    if "/" in token or "\\" in token:
        raise ValueError(
            "Shot code must be a single folder name without path separators"
        )

    return token


def build_shot_path(shot_code: str | None) -> str:
    """Build the canonical relative shot path: `shot/<shot_code>`."""
    return "/".join(("shot", validate_shot_code_token(shot_code)))


def split_csv_set(value: str | None) -> set[str]:
    """Parse a comma-separated ShotGrid string into normalized variant tokens.

    Used by the entity classes in `pipe.shotgrid.entities` to convert
    ShotGrid's CSV string fields (`sg_material_variants`, etc.) into Python
    sets.  Public because it's straightforward and a reader looking for
    "how does the pipeline parse SG variant strings" should find it here.
    """
    if not value:
        return set()
    return {token.strip() for token in value.split(",") if token.strip()}
