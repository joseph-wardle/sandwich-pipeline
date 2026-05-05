from __future__ import annotations

import getpass
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from shared.util import get_production_path

log = logging.getLogger(__name__)


def _current_login_username() -> str:
    """Return the current login username with a robust fallback."""
    try:
        return os.getlogin()
    except OSError:
        return getpass.getuser()


def _username_mapping_path() -> Path:
    return get_production_path() / "json" / "usernames.json"


@lru_cache(maxsize=1)
def _username_display_map() -> dict[str, str]:
    """Load username->display name mappings from production JSON."""
    mapping_path = _username_mapping_path()
    if not mapping_path.exists():
        log.warning(
            "Username mapping file was not found at %s. Falling back to login usernames.",
            mapping_path,
        )
        return {}

    try:
        raw_data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception(
            "Could not load username mapping from %s. Falling back to login usernames.",
            mapping_path,
        )
        return {}

    if not isinstance(raw_data, dict):
        log.warning(
            "Username mapping file at %s must be a JSON object. Falling back to login usernames.",
            mapping_path,
        )
        return {}

    display_map: dict[str, str] = {}
    for username, display_name in raw_data.items():
        if not isinstance(username, str) or not isinstance(display_name, str):
            continue

        normalized_username = username.strip()
        normalized_display_name = display_name.strip()
        if not normalized_username or not normalized_display_name:
            continue

        display_map[normalized_username] = normalized_display_name
        display_map.setdefault(normalized_username.lower(), normalized_display_name)

    return display_map


def resolve_artist_display_name() -> str:
    """Resolve the artist display name with username fallback."""
    username = _current_login_username().strip()
    if not username:
        return ""

    username_map = _username_display_map()
    mapped_name = username_map.get(username)
    if mapped_name:
        return mapped_name

    return username_map.get(username.lower(), username)


__all__ = [
    "resolve_artist_display_name",
]
