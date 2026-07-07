"""Remembers the last folder an artist exported a playblast to with the
Custom Folder destination, so the dialogs can seed the field with it on the
next run. Stored per-user via QSettings (an INI under ~/.config), which works
identically in every Qt-hosting DCC and needs no DCC-specific machinery."""

from __future__ import annotations

from pathlib import Path

from Qt import QtCore

_SETTINGS_ORG = "sandwich-pipeline"
_SETTINGS_APP = "playblast"
_LAST_CUSTOM_FOLDER_KEY = "last_custom_folder"


def _settings() -> QtCore.QSettings:
    return QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def load_last_custom_folder() -> Path | None:
    """Return the remembered folder, or None if unset or gone from disk."""
    raw_value = str(_settings().value(_LAST_CUSTOM_FOLDER_KEY, "") or "").strip()
    if not raw_value:
        return None
    folder = Path(raw_value).expanduser()
    if not folder.is_dir():
        return None
    return folder


def save_last_custom_folder(folder: str | Path) -> None:
    folder_text = str(folder).strip()
    if not folder_text:
        return
    settings = _settings()
    settings.setValue(_LAST_CUSTOM_FOLDER_KEY, folder_text)
    settings.sync()


__all__ = ["load_last_custom_folder", "save_last_custom_folder"]
