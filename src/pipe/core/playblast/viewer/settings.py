"""Viewer-remembered artist choices, stored per-user via QSettings."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from Qt.QtCore import QSettings

from pipe.core.playblast.clip import DestinationId

_SETTINGS_ORG = "sandwich-pipeline"
_SETTINGS_APP = "playblast"
_LAST_CUSTOM_FOLDER_KEY = "last_custom_folder"
_CHECKED_KEY_PREFIX = "confirm_checked_v4"


def _settings() -> QSettings:
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def load_checked_destinations(settings_key: str) -> frozenset[DestinationId] | None:
    """Return the destinations checked on this tool's last Confirm, or None if
    nothing is remembered."""
    if not settings_key:
        return None
    raw = str(_settings().value(f"{_CHECKED_KEY_PREFIX}/{settings_key}", "") or "")
    if not raw:
        return None
    try:
        return frozenset(DestinationId(str(item)) for item in json.loads(raw))
    except (ValueError, TypeError):
        return None


def save_checked_destinations(settings_key: str, ids: Iterable[DestinationId]) -> None:
    if not settings_key:
        return
    settings = _settings()
    settings.setValue(f"{_CHECKED_KEY_PREFIX}/{settings_key}", json.dumps(sorted(ids)))
    settings.sync()


def load_last_custom_folder() -> Path | None:
    """Return the remembered browse folder, or None if unset or gone from disk."""
    raw = str(_settings().value(_LAST_CUSTOM_FOLDER_KEY, "") or "").strip()
    if not raw:
        return None
    folder = Path(raw).expanduser()
    if not folder.is_dir():
        return None
    return folder


def save_last_custom_folder(folder: Path) -> None:
    settings = _settings()
    settings.setValue(_LAST_CUSTOM_FOLDER_KEY, str(folder))
    settings.sync()


__all__ = [
    "load_checked_destinations",
    "load_last_custom_folder",
    "save_checked_destinations",
    "save_last_custom_folder",
]
