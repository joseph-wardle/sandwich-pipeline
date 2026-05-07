from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from Qt import QtWidgets
from shared.util import get_production_path

from pipe.glui.dialogs import MessageDialog

log = logging.getLogger(__name__)

# Location of the animation lock list in the production output folder.
LOCK_FILE = get_production_path() / "json" / "locks.json"

# Simple, hardcoded password to prevent accidental republishes.
REPUBLISH_PASSWORD = "steveisreallycool"


def _normalize_codes(codes: Iterable[str]) -> set[str]:
    return {code.strip().upper() for code in codes if str(code).strip()}


def load_locked_sequences(lock_file: Path = LOCK_FILE) -> set[str]:
    try:
        raw_text = lock_file.read_text()
    except FileNotFoundError:
        return set()
    except Exception as exc:
        log.warning("Failed to read anim lock file '%s': %s", lock_file, exc)
        return set()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse anim lock file '%s': %s", lock_file, exc)
        return set()

    if not isinstance(data, dict):
        log.warning("Anim lock file '%s' must contain a JSON object.", lock_file)
        return set()

    sequences = data.get("anim_locked_sequences", [])
    if not isinstance(sequences, list):
        log.warning(
            "Anim lock file '%s' key 'anim_locked_sequences' must be a list.",
            lock_file,
        )
        return set()

    return _normalize_codes(sequences)


def is_shot_locked(
    sequence_code: str | None,
    shot_code: str | None,
    lock_file: Path = LOCK_FILE,
) -> bool:
    locked_sequences = load_locked_sequences(lock_file)
    if not locked_sequences:
        return False

    if sequence_code and sequence_code.strip().upper() in locked_sequences:
        return True

    if not shot_code:
        return False

    shot_code_upper = shot_code.strip().upper()
    return any(shot_code_upper.startswith(prefix) for prefix in locked_sequences)


def confirm_anim_republish_allowed(
    parent: QtWidgets.QWidget | None,
    sequence_code: str | None,
    shot_code: str | None,
    publish_path: Path | None,
    lock_file: Path = LOCK_FILE,
) -> bool:
    details = []
    if shot_code:
        details.append(f"Shot: {shot_code}")
    if sequence_code:
        details.append(f"Sequence: {sequence_code}")
    if publish_path:
        details.append(f"Target: {publish_path}")

    message = "Confirm animation publish?"
    if details:
        message = f"{message}\n" + "\n".join(details)

    confirm = MessageDialog(
        parent,
        message,
        "Confirm Publish",
        has_cancel_button=True,
    )
    if confirm.exec_() != QtWidgets.QDialog.Accepted:
        return False

    if not sequence_code and not shot_code:
        return True

    if not is_shot_locked(sequence_code, shot_code, lock_file=lock_file):
        return True

    if publish_path is not None and not publish_path.exists():
        # Not a republish, allow without password.
        return True

    text, ok = QtWidgets.QInputDialog.getText(
        parent,
        "Animation Publish Locked",
        f"Sequence '{sequence_code or shot_code}' is animation locked.\n"
        "Enter password to republish:",
        QtWidgets.QLineEdit.Password,
    )

    if not ok:
        return False

    if text != REPUBLISH_PASSWORD:
        MessageDialog(
            parent,
            "Incorrect password. Publish canceled.",
            "Publish Locked",
        ).exec_()
        return False

    return True
